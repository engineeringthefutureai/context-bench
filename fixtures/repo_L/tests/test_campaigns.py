from datetime import date

from shopkit.marketing.campaigns import Campaign, campaign_handle


def test_campaign_handle():
    assert campaign_handle("Black Friday 2026!") == "black_friday_2026"
    assert len(campaign_handle("word " * 20)) == 30


def test_campaign_link_and_window():
    c = Campaign("Spring Launch", date(2026, 3, 1), date(2026, 3, 31), channel="social")
    assert c.link("/new").endswith("/new?utm_source=social&utm_medium=social&utm_campaign=spring_launch")
    assert c.running(date(2026, 3, 31)) and not c.running(date(2026, 4, 1))
