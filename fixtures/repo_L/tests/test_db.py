import sqlite3
from pathlib import Path

import pytest

from shopkit.catalog.models import Product
from shopkit.customers.models import Customer
from shopkit.db import connect
from shopkit.db.migrations import MigrationError, MigrationRunner, discover, split_statements
from shopkit.db.query import Select, insert, update
from shopkit.db.repositories import (AuditRepository, CustomerRepository, OrderRepository,
                                     ProductNotFound, ProductRepository)
from shopkit.errors import DuplicateOrder, OrderNotFound
from shopkit.orders.models import Order, OrderLine


@pytest.fixture
def db():
    d = connect()
    yield d
    d.close()


def test_all_migrations_apply_in_order_and_are_recorded(db):
    runner = MigrationRunner(db)
    versions = [m.version for m in discover()]
    assert versions == sorted(versions) and versions[0] == 1
    assert runner.current_version() == versions[-1] and runner.pending() == []
    assert all(row["applied"] for row in runner.status())


def test_schema_has_the_columns_later_migrations_added(db):
    cols = {r["name"] for r in db.query("PRAGMA table_info(orders)")}
    assert {"subtotal_cents", "total_cents", "channel"} <= cols
    assert db.table_exists("payout_holds") and db.table_exists("journal_lines")


def test_changed_migration_is_refused(tmp_path):
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x INTEGER);\n")
    d = connect(migrations_dir=tmp_path)
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x INTEGER, y INTEGER);\n")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        MigrationRunner(d, tmp_path).pending()


def test_failed_migration_rolls_back(tmp_path):
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x INTEGER);\nINSERT INTO nope VALUES (1);\n")
    d = connect(migrate=False)
    with pytest.raises(MigrationError):
        MigrationRunner(d, tmp_path).apply_all()
    assert not d.table_exists("a") and MigrationRunner(d, tmp_path).current_version() == 0


def test_requires_header_and_duplicate_versions(tmp_path):
    (tmp_path / "0002_b.sql").write_text("-- requires: 0001\nCREATE TABLE b (x INTEGER);\n")
    d = connect(migrate=False)
    with pytest.raises(MigrationError, match="requires migration 0001"):
        MigrationRunner(d, tmp_path).apply_all()
    (tmp_path / "0002_c.sql").write_text("CREATE TABLE c (x INTEGER);\n")
    with pytest.raises(MigrationError, match="two migrations"):
        discover(tmp_path)


def test_split_statements_ignores_comments_and_keeps_multiline():
    sql = "-- c\nCREATE TABLE t (\n  a INTEGER\n);\n\nINSERT INTO t VALUES (1);\n"
    assert split_statements(sql) == ["CREATE TABLE t (\n  a INTEGER\n)", "INSERT INTO t VALUES (1)"]


def test_select_builder_parameterises_and_validates():
    sql, params = (Select("orders").where("status = ?", "paid").where_in("currency", ["USD", "EUR"])
                   .order_by("created_at", desc=True).limit(10, 20).build())
    assert sql == ("SELECT * FROM orders WHERE (status = ?) AND (currency IN (?, ?)) "
                   "ORDER BY created_at DESC LIMIT 10 OFFSET 20")
    assert params == ("paid", "USD", "EUR")
    assert Select("orders").where_in("id", []).build()[0].endswith("WHERE (0 = 1)")
    with pytest.raises(ValueError):
        Select("orders; DROP TABLE orders")
    with pytest.raises(ValueError):
        Select("orders").where("a = ? AND b = ?", 1)
    assert insert("t", {"a": 1})[0] == "INSERT INTO t (a) VALUES (?)"
    assert update("t", {"a": 1}, "id = ?", 5) == ("UPDATE t SET a = ? WHERE id = ?", (1, 5))


def test_order_repository_round_trip_and_history(db):
    ProductRepository(db).add(Product("SKU-1", "Mug", 1200))
    CustomerRepository(db).add(Customer("C1", " Ann@Example.com ", "Ann"))
    repo = OrderRepository(db)
    repo.add(Order("O1", [OrderLine("SKU-1", 2, 1200)]), customer_id="C1")
    assert repo.get("O1").total_cents == 2400 and repo.status("O1") == "new"
    repo.set_status("O1", "paid", actor="api")
    assert [h[0] for h in repo.history("O1")] == ["new", "paid"]
    page = repo.page(customer_id="C1")
    assert page.total == 1 and page.rows[0].total_cents == 2400 and not page.has_more
    with pytest.raises(DuplicateOrder):
        repo.add(Order("O1"))
    with pytest.raises(OrderNotFound):
        repo.get("nope")


def test_transaction_rolls_back_order_and_lines_together(db):
    repo = OrderRepository(db)
    with pytest.raises(sqlite3.IntegrityError):
        repo.add(Order("O2", [OrderLine("SKU-1", 0, 100)]))      # qty CHECK fails on the line
    assert db.scalar("SELECT COUNT(*) FROM orders WHERE id = 'O2'") == 0


def test_products_and_customers(db):
    products, customers = ProductRepository(db), CustomerRepository(db)
    products.add(Product("A", "Apple", 100))
    products.set_price("A", 150)
    assert products.get("A").price_cents == 150 and list(products.by_skus(["A", "B"])) == ["A"]
    with pytest.raises(ProductNotFound):
        products.set_price("B", 1)
    customers.add(Customer("C9", "Bo@Example.com", "Bo"))
    assert customers.find_by_email("bo@example.com").id == "C9"


def test_audit_repository(db):
    audit = AuditRepository(db)
    audit.record("admin", "price.changed", "product:A", old=100, new=150)
    assert audit.for_subject("product:A")[0]["details"] == {"new": 150, "old": 100}


def test_stats_count_queries(db):
    before = db.stats.queries
    db.query("SELECT 1")
    assert db.stats.queries == before + 1 and db.stats.as_dict()["last_error"] is None
