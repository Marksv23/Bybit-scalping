"""Synthetic test fixtures. NOT market data — used only to verify the math and rendering."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from scalpscan.classify import classify
from scalpscan.models import AccountInfo, Bar, MarketData, Quote, SymbolSpec, TickStats

NOW = datetime(2026, 9, 29, 13, 30, tzinfo=timezone.utc)  # Tuesday, London+NY overlap


def spec(symbol: str, *, point: float, tick_value: float, contract: float = 100_000,
         vmin: float = 0.01, digits: int = 5, profit: str = "USD", base: str = "") -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol, asset_class=classify(symbol), description="", path="", digits=digits,
        point=point, tick_size=point, tick_value_loss=tick_value, tick_value_profit=tick_value,
        contract_size=contract, volume_min=vmin, volume_step=vmin, volume_max=100,
        currency_base=base, currency_profit=profit, currency_margin=base or profit,
        swap_long=-1.0, swap_short=0.5, swap_mode="points", trade_enabled=True,
    )


def bars(minutes: int, n: int, price: float, rng: float, recent_rng: float | None = None,
         recent: int = 30, spread_pts: int = 10) -> list[Bar]:
    out = []
    start = NOW - timedelta(minutes=minutes * n)
    for i in range(n):
        r = recent_rng if (recent_rng is not None and i >= n - recent - 1) else rng
        o = price + (r / 4 if i % 2 else -r / 4)
        out.append(Bar(start + timedelta(minutes=minutes * i), o, o + r / 2, o - r / 2, o, 100, spread_pts))
    return out


def market(sp: SymbolSpec, *, bid: float, spread: float, rng1: float, rng5: float, margin: float,
           ticks_per_5m: int = 600, recent_rng1: float | None = None, age_sec: float = 1,
           median_spread: float | None = None) -> MarketData:
    med = median_spread if median_spread is not None else spread
    return MarketData(
        spec=sp,
        quote=Quote(bid, bid + spread, NOW - timedelta(seconds=age_sec)),
        bars_m1=bars(1, 242, bid, rng1, recent_rng1),
        bars_m5=bars(5, 60, bid, rng5),
        bars_m15=bars(15, 60, bid, rng5 * 1.8),
        tick_stats=TickStats(300, ticks_per_5m, med, med * 1.2, med * 1.5),
        margin_min_lot=margin,
        fetched_at_utc=NOW,
    )


class FakeProvider:
    offset_note = "test"

    def __init__(self, data: dict[str, MarketData]):
        self.data = data

    @property
    def offset_hours(self) -> float:
        return 0.0

    stale_note = None

    def calendar(self):
        return None

    def refresh(self) -> None:
        pass

    def account(self) -> AccountInfo:
        return AccountInfo(1, "Bybit-Test", "Test Co", "USD", 500, 30.0, 30.0, "DEMO")

    def symbols(self) -> list[SymbolSpec]:
        return [md.spec for md in self.data.values()]

    def resolve(self, name: str) -> str | None:
        for n in self.data:
            if n.lower() in (name.lower(), name.lower() + ".s"):
                return n
        return None

    def snapshot(self, sp: SymbolSpec) -> MarketData:
        return replace(self.data[sp.symbol])


def universe() -> dict[str, MarketData]:
    return {
        # FX: 0.01 lot, 1 point = $0.001; spread 10 pts, 1m range 40 pts
        "EURUSD.s": market(spec("EURUSD.s", point=0.00001, tick_value=1.0, base="EUR"),
                           bid=1.10000, spread=0.00010, rng1=0.00040, rng5=0.00100, margin=2.2),
        # Oil: spread 23 pts but tiny tick value at min lot
        "USOUSD.s": market(spec("USOUSD.s", point=0.001, tick_value=0.1, contract=100, vmin=0.1, digits=3),
                           bid=70.000, spread=0.023, rng1=0.060, rng5=0.150, margin=1.4),
        # AUDJPY: spread 22 pts, JPY tick value
        "AUDJPY.s": market(spec("AUDJPY.s", point=0.001, tick_value=0.66, digits=3, profit="JPY", base="AUD"),
                           bid=98.000, spread=0.022, rng1=0.030, rng5=0.070, margin=1.3),
        # Gold: margin too big for $30
        "XAUUSD.s": market(spec("XAUUSD.s", point=0.01, tick_value=1.0, contract=100, digits=2),
                           bid=2650.00, spread=0.20, rng1=1.20, rng5=3.00, margin=13.3),
        # Illiquid exotic with cost eating the move
        "USDTRY.s": market(spec("USDTRY.s", point=0.00001, tick_value=0.03, base="USD", profit="TRY"),
                           bid=34.00000, spread=0.01000, rng1=0.00500, rng5=0.01200, margin=6.0,
                           ticks_per_5m=10),
    }
