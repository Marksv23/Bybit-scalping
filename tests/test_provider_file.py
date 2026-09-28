"""FileProvider against a snapshot in the exact schema ScalpScanExporter.mq5 writes (synthetic values)."""
import json
import time

import pytest

from scalpscan import provider_file
from scalpscan.cli import App, make_provider, parse
from scalpscan.config import Settings
from scalpscan.provider_file import FileProvider
from scalpscan.provider_mt5 import ProviderError
from scalpscan.scanner import Scanner


def bars(now, step, n, price, rng):
    return [[now - (n - i) * step, price, price + rng / 2, price - rng / 2, price, 100, 10] for i in range(n)]


def snapshot(now=None, calendar=()):
    now = int(now or time.time())
    sym = {
        "name": "EURUSD.s", "description": "Euro vs \"Dollar\"", "path": "Forex\\Majors\\EURUSD.s",
        "digits": 5, "point": 1e-5, "tick_size": 1e-5, "tick_value": 1.0, "tick_value_loss": 1.0,
        "tick_value_profit": 1.0, "contract_size": 100000, "volume_min": 0.01, "volume_step": 0.01,
        "volume_max": 100, "currency_base": "EUR", "currency_profit": "USD", "currency_margin": "EUR",
        "swap_long": -7.1, "swap_short": 2.3, "swap_mode": 1, "trade_mode": 4,
        "bid": 1.1, "ask": 1.1001, "quote_utc_ms": (now - 1) * 1000, "margin_min_lot": 2.2,
        "ticks": {"window_sec": 300, "count": 900, "spread_median": 1e-4, "spread_p90": 1.2e-4,
                  "spread_max": 1.5e-4},
        "m1": bars(now, 60, 242, 1.1, 4e-4), "m5": bars(now, 300, 60, 1.1, 1e-3),
        "m15": bars(now, 900, 60, 1.1, 1.8e-3),
    }
    closed = dict(sym, name="GER40.s", path="Indices\\GER40.s", quote_utc_ms=(now - 7200) * 1000,
                  currency_profit="EUR", currency_base="EUR")
    return {
        "schema": 1, "exporter": "ScalpScanExporter 1.00", "generated_utc": now, "server_offset_sec": 10800,
        "tick_window_sec": 300,
        "account": {"login": 1, "server": "Bybit-Live", "company": "Bybit", "currency": "USD",
                    "leverage": 500, "balance": 30.0, "free_margin": 30.0, "trade_mode": 0},
        "calendar": list(calendar), "symbols": [sym, closed],
    }


@pytest.fixture
def snap(tmp_path):
    def write(doc):
        p = tmp_path / "scalpscan" / "snapshot.json"
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(doc), encoding="utf-8")
        return p
    return write


def test_scan_from_snapshot(snap):
    now = int(time.time())
    path = snap(snapshot(now, [{"t": now + 600, "currency": "USD", "title": "CPI m/m", "impact": "High"}]))
    s = Settings(snapshot_path=str(path))
    p = make_provider(s)
    assert isinstance(p, FileProvider)
    p.connect()
    assert p.offset_hours == 3 and p.stale_note is None
    out = App(Scanner(p, s), s).execute(parse("SCAN"))
    assert "MT5 built-in economic calendar" in out
    assert "⚠ HIGH EVENT RISK" in out  # USD CPI in 10 min hits EURUSD
    assert "**EURUSD.s** (Forex) ⚠" in out
    assert "GER40.s" in out.split("### AVOID NOW")[1] or "GER40.s" in out.split("LIVE DATA UNAVAILABLE")[-1]
    why = App(Scanner(p, s), s).execute(parse("WHY eurusd"))
    assert "Forex\\Majors\\EURUSD.s" in why and "$2.20" in why


def test_stale_snapshot_warns(snap):
    path = snap(snapshot(time.time() - 600))
    s = Settings(snapshot_path=str(path), calendar_enabled=False)
    p = FileProvider(s)
    p.connect()
    out = App(Scanner(p, s), s).execute(parse("SCAN"))
    assert "советник ScalpScanExporter не работает" in out
    assert "Рейтинг не строится" in out


def test_autodiscovery_macos_layout(tmp_path, monkeypatch):
    support = tmp_path / "Library" / "Application Support"
    target = (support / "net.metaquotes.wine.metatrader5" / "drive_c" / "users" / "user" / "AppData"
              / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files" / "scalpscan" / "snapshot.json")
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(snapshot()), encoding="utf-8")
    (support / "SomeOtherApp" / "scalpscan").mkdir(parents=True)
    (support / "SomeOtherApp" / "scalpscan" / "snapshot.json").write_text("{}")
    monkeypatch.setattr(provider_file, "SEARCH_ROOTS", [support])
    assert provider_file.find_snapshot() == target


def test_missing_snapshot_is_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_file, "SEARCH_ROOTS", [tmp_path])
    with pytest.raises(ProviderError, match="ScalpScanExporter"):
        FileProvider(Settings()).connect()
