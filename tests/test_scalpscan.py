from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from fakes import NOW, FakeProvider, bars, market, spec, universe
from scalpscan.calendar import events_near, parse_events
from scalpscan.classify import classify, exposure_currencies
from scalpscan.cli import App, parse
from scalpscan.config import Settings
from scalpscan.indicators import atr, avg_range
from scalpscan.metrics import evaluate, lin
from scalpscan.models import EconEvent
from scalpscan.report import render_scan
from scalpscan.scanner import Scanner
from scalpscan.sessions import active_sessions, session_label


@pytest.fixture
def s():
    return Settings(calendar_enabled=False)


def test_lin_both_directions():
    assert lin(0.05, 0.05, 0.4) == 1 and lin(0.4, 0.05, 0.4) == 0
    assert lin(0.225, 0.05, 0.4) == pytest.approx(0.5)
    assert lin(120, 120, 5) == 1 and lin(1, 120, 5) == 0
    assert lin(None, 1, 0) == 0


def test_atr_and_range_use_closed_bars():
    b = bars(1, 50, 100.0, 2.0)
    assert avg_range(b, 10) == pytest.approx(2.0)
    assert atr(b, 14) == pytest.approx(2.0, rel=0.3)
    assert atr(b[:10], 14) is None


def test_spread_cost_is_money_not_points(s):
    u = universe()
    oil = evaluate(u["USOUSD.s"], s, NOW, [])
    aud = evaluate(u["AUDJPY.s"], s, NOW, [])
    assert oil.spread_points == pytest.approx(23) and aud.spread_points == pytest.approx(22)
    # 23 ticks * $0.1/tick/lot * 0.1 lot  vs  22 ticks * $0.66 * 0.01 lot
    assert oil.spread_cost == pytest.approx(0.23)
    assert aud.spread_cost == pytest.approx(0.1452)
    # but relative to movement AUDJPY is the costlier one
    assert aud.cost_ratio_m5 > oil.cost_ratio_m5


def test_round_trip_includes_commission():
    s = Settings(calendar_enabled=False, commission_per_lot_side=3.0)
    r = evaluate(universe()["EURUSD.s"], s, NOW, [])
    assert r.commission_rt == pytest.approx(0.06)  # 3 * 0.01 lot * 2 sides
    assert r.rt_cost == pytest.approx(0.10 + 0.06)
    assert r.breakeven_ticks == pytest.approx(16)
    assert r.rt_cost_price == pytest.approx(0.00016)


def test_filters(s):
    u = universe()
    gold = evaluate(u["XAUUSD.s"], s, NOW, [])
    assert not gold.ok and gold.margin_fit == "не подходит"
    tr = evaluate(u["USDTRY.s"], s, NOW, [])
    assert not tr.ok and any("активность" in x for x in tr.excluded_reasons)
    stale = evaluate(replace(u["EURUSD.s"], quote=replace(u["EURUSD.s"].quote,
                                                          time_utc=NOW - timedelta(minutes=10))), s, NOW, [])
    assert not stale.ok and "LIVE DATA UNAVAILABLE" in stale.excluded_reasons[0]
    wide = evaluate(market(u["EURUSD.s"].spec, bid=1.1, spread=0.0004, rng1=0.0004, rng5=0.001,
                           margin=2.2, median_spread=0.0001), s, NOW, [])
    assert any("спред расширен" in x for x in wide.excluded_reasons)


def test_missing_data_is_reported_not_invented(s):
    md = replace(universe()["EURUSD.s"], quote=None, margin_min_lot=None, tick_stats=None)
    r = evaluate(md, s, NOW, [])
    assert not r.ok
    assert r.spread_cost is None and r.margin is None
    assert any("LIVE DATA UNAVAILABLE" in x for x in r.data_issues)
    assert any("MARGIN UNAVAILABLE" in x for x in r.data_issues)


def test_high_volatility_is_not_rewarded(s):
    sp = universe()["EURUSD.s"].spec
    normal = evaluate(market(sp, bid=1.1, spread=0.0001, rng1=0.0004, rng5=0.001, margin=2.2), s, NOW, [])
    hot = evaluate(market(sp, bid=1.1, spread=0.0001, rng1=0.0004, rng5=0.001, margin=2.2,
                          recent_rng1=0.0016), s, NOW, [])
    assert hot.rel_activity > 3
    assert hot.score.components["activity"] < normal.score.components["activity"]


def test_event_risk_flag_and_penalty(s):
    ev = [EconEvent(NOW + timedelta(minutes=10), "USD", "CPI m/m", "High")]
    base = evaluate(universe()["EURUSD.s"], s, NOW, [])
    flagged = evaluate(universe()["EURUSD.s"], s, NOW, ev)
    assert flagged.high_event_risk and flagged.ok
    assert flagged.score.total == pytest.approx(round(base.score.total * s.event_score_factor, 1), abs=0.2)
    far = [EconEvent(NOW + timedelta(hours=5), "USD", "CPI", "High")]
    assert not evaluate(universe()["EURUSD.s"], s, NOW, far).high_event_risk


def test_calendar_parsing():
    raw = [
        {"title": "CPI m/m", "country": "USD", "date": "2026-09-29T08:30:00-04:00", "impact": "High"},
        {"title": "Minor", "country": "USD", "date": "2026-09-29T09:00:00-04:00", "impact": "Low"},
    ]
    ev = parse_events(raw)
    assert len(ev) == 1 and ev[0].time_utc == datetime(2026, 9, 29, 12, 30, tzinfo=timezone.utc)
    assert events_near(ev, {"USD"}, ev[0].time_utc - timedelta(minutes=5), 30, 10)
    assert not events_near(ev, {"JPY"}, ev[0].time_utc, 30, 10)


def test_classify_and_exposure():
    assert classify("EURUSD.s") == "Forex"
    assert classify("XAUUSD.s") == "Commodities"
    assert classify("USOUSD.s") == "Commodities"
    assert classify("US500.s") == "Index"
    assert classify("AAPL.s") == "Stocks"
    assert classify("ANY", path="Indices\\US") == "Index"
    assert exposure_currencies("GER40.s", "Index", "EUR") == {"EUR"}
    assert exposure_currencies("AUDJPY.s", "Forex", "JPY", "AUD") == {"AUD", "JPY"}


def test_sessions_dst_aware():
    assert session_label(NOW) == "London + New York overlap"
    assert active_sessions(datetime(2026, 10, 3, 12, tzinfo=timezone.utc)) == []  # Saturday
    assert "Tokyo" in active_sessions(datetime(2026, 9, 29, 2, tzinfo=timezone.utc))


def test_parse_commands():
    assert parse("scan").kind == "scan"
    assert parse("SCAN forex").asset_class == "Forex"
    assert parse("COMPARE USOUSD.s AUDJPY.s").symbols == ("USOUSD.s", "AUDJPY.s")
    assert parse("WHY XAUUSD").symbols == ("XAUUSD",)
    assert parse("REFRESH").kind == "refresh"
    with pytest.raises(ValueError):
        parse("SCAN BONDS")


def test_full_scan_report(s):
    app = App(Scanner(FakeProvider(universe()), s, clock=lambda: NOW), s)
    out = app.execute(parse("SCAN"))
    for section in ("Analysis time:", "Data freshness:", "TOP 10 — RIGHT NOW", "BEST FOR $30",
                    "Наиболее эффективный по заданным критериям прямо сейчас.", "WATCH NOW", "AVOID NOW",
                    "Дополнительный анализ", "P/L при +0.1%", "P/L при +0.25%"):
        assert section in out, section
    top_part = out.split("### Почему")[0]
    assert "XAUUSD.s" not in top_part  # excluded by margin
    assert "CALENDAR DISABLED" in out
    assert app.execute(parse("REFRESH")).startswith("# SCAN")


def test_compare_and_why(s):
    app = App(Scanner(FakeProvider(universe()), s, clock=lambda: NOW), s)
    cmp_ = app.execute(parse("COMPARE USOUSD AUDJPY.s"))
    assert "COMPARE USOUSD.s vs AUDJPY.s" in cmp_ and "голый spread" in cmp_
    why = app.execute(parse("WHY XAUUSD"))
    assert "Score breakdown" in why and "Исключён" not in why and "Фильтры:** маржа" in why
    assert "не найдены" in app.execute(parse("WHY NOPE"))


def test_no_live_data_banner(s):
    u = {k: replace(v, quote=replace(v.quote, time_utc=NOW - timedelta(hours=40))) for k, v in universe().items()}
    res = Scanner(FakeProvider(u), s, clock=lambda: NOW).run("SCAN")
    out = render_scan(res, s)
    assert "LIVE DATA UNAVAILABLE" in out and not res.ranked
