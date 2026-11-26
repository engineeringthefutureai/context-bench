import sys
from datetime import date, timedelta

import pytest

import shopkit.config as config
from shopkit.orders.models import Order, OrderLine
from shopkit.orders.returns import ReturnNotAllowed, open_return


def test_setting_defaults_to_30_days():
    assert config.load_settings({}).return_window_days == 30


def test_setting_comes_from_the_environment():
    assert config.load_settings({"SHOPKIT_RETURN_DAYS": "14"}).return_window_days == 14


def _use(settings, monkeypatch):
    """Install `settings` wherever the shop holds the current settings object."""
    original = config.SETTINGS
    for mod in list(sys.modules.values()):
        if getattr(mod, "__name__", "").startswith("shopkit"):
            for attr, value in list(vars(mod).items()):
                if value is original:
                    monkeypatch.setattr(mod, attr, settings)
                # a function that takes the settings as a default argument (settings: Settings = SETTINGS)
                defaults = getattr(value, "__defaults__", None)
                if defaults and any(d is original for d in defaults):
                    monkeypatch.setattr(value, "__defaults__",
                                        tuple(settings if d is original else d for d in defaults))
                kwdefaults = getattr(value, "__kwdefaults__", None)
                if kwdefaults and any(d is original for d in kwdefaults.values()):
                    monkeypatch.setattr(value, "__kwdefaults__",
                                        {k: settings if d is original else d for k, d in kwdefaults.items()})


def _open(days_after_delivery):
    delivered = date(2026, 11, 1)
    order = Order("o1", [OrderLine("MUG", 1, 1000)])
    return open_return(order, delivered, {"MUG": 1}, "damaged", delivered + timedelta(days=days_after_delivery))


def test_open_return_follows_the_setting(monkeypatch):
    _use(config.load_settings({"SHOPKIT_RETURN_DAYS": "14"}), monkeypatch)
    _open(14)
    with pytest.raises(ReturnNotAllowed):
        _open(20)


def test_default_window_unchanged(monkeypatch):
    _use(config.load_settings({}), monkeypatch)
    _open(30)
    with pytest.raises(ReturnNotAllowed):
        _open(31)
