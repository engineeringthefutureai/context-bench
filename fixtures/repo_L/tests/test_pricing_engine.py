from datetime import date, datetime

import pytest

from shopkit.catalog.models import Product
from shopkit.pricing.currency import Rate, RateTable, RateUnavailable, convert_cents, parse_rate
from shopkit.pricing.endings import apply_ending, has_ending
from shopkit.pricing.engine import PricingEngine
from shopkit.pricing.price_lists import NoPrice, PriceList, PriceListImportError, PriceListSet, export_csv, import_csv
from shopkit.pricing.schedules import Schedule, ScheduleConflict, ScheduledPrice

NOW = datetime(2026, 3, 10, 12, 0)


def test_convert_cents_rounds_half_to_even():
    assert convert_cents(1000, 921_500) == 922
    assert convert_cents(1, 500_000) == 0 and convert_cents(3, 500_000) == 2      # 0.5 -> 0, 1.5 -> 2
    assert parse_rate("0.9215") == 921_500
    with pytest.raises(ValueError):
        parse_rate("0.12345678")


def test_rate_table_falls_back_to_earlier_days_and_inverse():
    rt = RateTable()
    rt.add(Rate("USD", "EUR", date(2026, 3, 6), 920_000))           # Friday
    assert rt.rate("USD", "EUR", date(2026, 3, 9)).rate_micros == 920_000   # Monday uses Friday
    assert rt.rate("EUR", "USD", date(2026, 3, 9)).rate_micros == 1_086_957
    with pytest.raises(RateUnavailable):
        rt.rate("USD", "EUR", date(2026, 3, 20))
    assert rt.convert(500, "USD", "USD", date(2026, 3, 9)) == 500


def test_price_endings():
    assert apply_ending(2437, "USD") == 2499 and apply_ending(50, "USD") == 50
    assert apply_ending(2437, "EUR") == 2495          # +2.4%: within the 3% cap, round up
    assert apply_ending(2397, "EUR") == 2395          # 24.95 would be +4.1%: round down to 23.95
    assert apply_ending(12_430, "EUR") == 12_500 and apply_ending(4_120, "GBP") == 4_199
    assert has_ending(1999, "USD") and not has_ending(1950, "USD")


def test_price_list_resolution_by_tier_priority_and_breaks():
    retail = PriceList("EUR-retail", "EUR", priority=0)
    gold = PriceList("EUR-gold", "EUR", customer_tier="gold", priority=0)
    retail.add("MUG", 1295)
    retail.add("MUG", 1095, min_qty=10)
    gold.add("MUG", 1195)
    sets = PriceListSet([retail, gold])
    assert sets.resolve("MUG", 1, "EUR", "standard") == (1295, "EUR-retail")
    assert sets.resolve("MUG", 1, "EUR", "gold") == (1195, "EUR-gold")
    assert sets.resolve("MUG", 12, "EUR", "standard") == (1095, "EUR-retail")
    with pytest.raises(NoPrice):
        sets.resolve("MUG", 1, "GBP")


def test_csv_import_is_all_or_nothing():
    pl = PriceList("USD-retail", "USD")
    assert import_csv("sku,min_qty,price\nA,1,12.99\nA,5,11.99\n", pl) == 2
    with pytest.raises(PriceListImportError) as e:
        import_csv("sku,min_qty,price\nB,1,3.99\nC,x,1.00\n", pl)
    assert "line 3" in e.value.details["errors"][0] and "B" not in pl.entries
    assert export_csv(pl) == "sku,min_qty,price\nA,1,12.99\nA,5,11.99\n"


def test_schedule_sales_and_changes():
    s = Schedule()
    s.add(ScheduledPrice("MUG", "USD", 999, datetime(2026, 3, 1), datetime(2026, 3, 15), "Spring"))
    s.add(ScheduledPrice("MUG", "USD", 899, datetime(2026, 3, 5), datetime(2026, 3, 7), "Flash"))
    s.add(ScheduledPrice("MUG", "USD", 1399, datetime(2026, 1, 1)))
    assert s.best_sale("MUG", "USD", NOW).label == "Spring"
    assert s.best_sale("MUG", "USD", datetime(2026, 3, 6)).unit_cents == 899
    assert s.base_price("MUG", "USD", NOW).unit_cents == 1399
    with pytest.raises(ScheduleConflict):
        s.add(ScheduledPrice("MUG", "USD", 1499, datetime(2026, 1, 1)))
    assert s.expire(datetime(2026, 3, 8)) == 1


def test_engine_precedence_conversion_and_floor():
    rates = RateTable()
    rates.add(Rate("USD", "EUR", date(2026, 3, 10), 920_000))
    engine = PricingEngine(rates=rates, costs={"MUG": 1000})
    mug = Product("MUG", "Mug", 1500)
    assert engine.price(mug, at=NOW).source == "list_price"
    eur = engine.price(mug, currency="EUR", at=NOW)
    assert (eur.unit_cents, eur.source) == (1395, "converted")      # 13.80 -> 13.95
    engine.schedule.add(ScheduledPrice("MUG", "USD", 900, datetime(2026, 3, 1), datetime(2026, 3, 31), "Sale"))
    sale = engine.price(mug, at=NOW)
    assert sale.unit_cents == 1050 and sale.floored and sale.compare_at_cents == 1500   # floor: cost + 5%
    engine.costs.clear()
    assert engine.price(mug, at=NOW).unit_cents == 900
