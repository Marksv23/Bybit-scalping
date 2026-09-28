"""Exercises MT5Provider against a stub `MetaTrader5` module shaped like the real API
(namedtuples + numpy structured arrays). Server time is simulated at UTC+3."""
import sys
import time
import types
from collections import namedtuple

import pytest

np = pytest.importorskip("numpy")

from scalpscan.config import Settings  # noqa: E402
from scalpscan.metrics import evaluate  # noqa: E402

OFFSET = 3 * 3600
SymbolInfo = namedtuple("SymbolInfo", [
    "name", "description", "path", "digits", "point", "trade_tick_size", "trade_tick_value",
    "trade_tick_value_profit", "trade_tick_value_loss", "trade_contract_size", "volume_min",
    "volume_step", "volume_max", "currency_base", "currency_profit", "currency_margin", "swap_long",
    "swap_short", "swap_mode", "trade_mode", "visible", "time"])
Tick = namedtuple("Tick", "time bid ask time_msc")
Account = namedtuple("Account", "login trade_mode leverage balance margin_free currency server company")
RATE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8"),
                 ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])
TICK = np.dtype([("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"), ("volume", "<u8"),
                 ("time_msc", "<i8"), ("flags", "<u4"), ("volume_real", "<f8")])


def make_stub():
    now_srv = int(time.time()) + OFFSET
    m = types.ModuleType("MetaTrader5")
    m.TIMEFRAME_M1, m.TIMEFRAME_M5, m.TIMEFRAME_M15 = 1, 5, 15
    m.COPY_TICKS_INFO, m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL = 2, 0, 1
    state = {"visible": False, "selected": []}

    def info(name="EURUSD.s"):
        return SymbolInfo(name, "Euro vs USD", "Forex\\Majors\\EURUSD.s", 5, 1e-5, 1e-5, 1.0, 1.0, 1.0,
                          100000, 0.01, 0.01, 100, "EUR", "USD", "EUR", -7.1, 2.3, 1, 4, state["visible"],
                          now_srv - 1)

    m.initialize = lambda **kw: True
    m.shutdown = lambda: None
    m.last_error = lambda: (0, "ok")
    m.account_info = lambda: Account(1, 0, 500, 30.0, 30.0, "USD", "Bybit-Demo", "Bybit")
    m.symbols_get = lambda group="*": (info(),)
    m.symbol_info = lambda name: info(name)

    def select(name, on):
        state["visible"] = on
        state["selected"].append(on)
        return True

    m.symbol_select = select
    m.symbol_info_tick = lambda name: Tick(now_srv - 1, 1.1, 1.1001, (now_srv - 1) * 1000)

    def rates(name, tf, start, count):
        a = np.zeros(count, dtype=RATE)
        a["time"] = now_srv - (count - np.arange(count)) * tf * 60
        a["open"] = a["close"] = 1.1
        a["high"], a["low"] = 1.1 + 0.0002 * tf, 1.1 - 0.0002 * tf
        a["tick_volume"], a["spread"] = 100, 10
        return a

    m.copy_rates_from_pos = rates

    def ticks(name, frm, count, flags):
        a = np.zeros(900, dtype=TICK)
        a["bid"], a["ask"] = 1.1, 1.1001
        return a

    m.copy_ticks_from = ticks
    m.order_calc_margin = lambda t, name, vol, price: 2.2
    return m, state


def test_provider_end_to_end(monkeypatch):
    stub, state = make_stub()
    monkeypatch.setitem(sys.modules, "MetaTrader5", stub)
    from scalpscan.provider_mt5 import MT5Provider

    s = Settings(calendar_enabled=False)
    p = MT5Provider(s)
    p.connect()
    assert p.offset_hours == 3.0
    assert p.resolve("eurusd") == "EURUSD.s"
    spec = p.symbols()[0]
    assert spec.asset_class == "Forex" and spec.trade_enabled
    md = p.snapshot(spec)
    assert state["selected"] == [True, False]  # market watch restored
    assert md.quote and abs(time.time() - md.quote.time_utc.timestamp()) < 5
    assert md.tick_stats.tick_count == 900 and md.margin_min_lot == 2.2
    r = evaluate(md, s, md.fetched_at_utc, [])
    assert r.ok, r.excluded_reasons
    assert r.spread_cost == pytest.approx(0.10)
    assert r.tick_rate_per_min == pytest.approx(180)
    assert p.account().mode == "DEMO"


def test_offset_from_config(monkeypatch):
    stub, _ = make_stub()
    monkeypatch.setitem(sys.modules, "MetaTrader5", stub)
    from scalpscan.provider_mt5 import MT5Provider

    p = MT5Provider(Settings(server_utc_offset_hours=2))
    p.connect()
    assert p.offset_hours == 2 and p.offset_note == "from config"
