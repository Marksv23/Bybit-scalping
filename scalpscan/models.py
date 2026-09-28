"""Data structures shared by the provider, the metric engine and the report."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

ASSET_CLASSES = ("Forex", "Index", "Commodities", "Stocks", "Crypto", "Other")


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    asset_class: str
    description: str
    path: str
    digits: int
    point: float
    tick_size: float
    # Account-currency value of one `tick_size` move for 1.0 lot. Loss-side value is used
    # for costs (conservative), profit-side value for P/L.
    tick_value_loss: float
    tick_value_profit: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    currency_base: str
    currency_profit: str
    currency_margin: str
    swap_long: float
    swap_short: float
    swap_mode: str
    trade_enabled: bool


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    time_utc: datetime  # already converted from broker server time to UTC


@dataclass(frozen=True)
class Bar:
    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread_points: int  # MT5 bar spread (points)


@dataclass(frozen=True)
class TickStats:
    """Statistics of the recent tick stream (real bid/ask updates)."""

    window_sec: int
    tick_count: int
    spread_median: float  # price units
    spread_p90: float
    spread_max: float


@dataclass
class MarketData:
    spec: SymbolSpec
    quote: Quote | None
    bars_m1: list[Bar]
    bars_m5: list[Bar]
    bars_m15: list[Bar]
    tick_stats: TickStats | None
    margin_min_lot: float | None  # account currency, from the terminal's margin calculator
    fetched_at_utc: datetime


@dataclass(frozen=True)
class AccountInfo:
    login: int | None
    server: str
    company: str
    currency: str
    leverage: int | None
    balance: float | None
    free_margin: float | None
    mode: str  # demo / contest / real


@dataclass(frozen=True)
class EconEvent:
    time_utc: datetime
    currency: str
    title: str
    impact: str


@dataclass
class ScoreBreakdown:
    """Each component is 0..1; weights live in Settings.weights."""

    components: dict[str, float] = field(default_factory=dict)
    weighted: dict[str, float] = field(default_factory=dict)
    event_factor: float = 1.0
    total: float = 0.0


@dataclass
class InstrumentResult:
    spec: SymbolSpec
    data: MarketData
    ok: bool = True
    excluded_reasons: list[str] = field(default_factory=list)
    data_issues: list[str] = field(default_factory=list)  # missing / non-live fields
    # prices & costs (min lot)
    lot: float = 0.0
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    quote_age_sec: float | None = None
    spread_price: float | None = None
    spread_points: float | None = None
    spread_ticks: float | None = None
    money_per_tick: float | None = None  # loss-side, min lot
    money_per_tick_profit: float | None = None
    spread_cost: float | None = None
    commission_rt: float = 0.0
    rt_cost: float | None = None
    rt_cost_price: float | None = None  # price move needed to cover round trip
    breakeven_ticks: float | None = None
    # volatility
    atr_m1: float | None = None
    atr_m5: float | None = None
    atr_m15: float | None = None
    range_m1: float | None = None
    range_m5: float | None = None
    range_m15: float | None = None
    range_m1_money: float | None = None
    range_m5_money: float | None = None
    cost_ratio_m1: float | None = None
    cost_ratio_m5: float | None = None
    cost_ratio_m15: float | None = None
    rel_activity: float | None = None  # recent 1m range vs 4h baseline
    tick_rate_per_min: float | None = None
    spread_vs_median: float | None = None
    spread_p90_vs_median: float | None = None
    # margin & risk
    margin: float | None = None
    margin_pct: float | None = None
    margin_fit: str = "n/a"  # подходит / условно подходит / не подходит
    notional: float | None = None
    effective_leverage: float | None = None
    stop_atr5_money: float | None = None
    stop_atr5_pct_deposit: float | None = None
    # context
    sessions_note: str = ""
    home_session_active: bool | None = None
    events: list[EconEvent] = field(default_factory=list)
    high_event_risk: bool = False
    score: ScoreBreakdown = field(default_factory=ScoreBreakdown)

    @property
    def symbol(self) -> str:
        return self.spec.symbol


@dataclass
class ScanResult:
    command: str
    analysis_time_utc: datetime
    account: AccountInfo | None
    server_utc_offset_hours: float
    active_sessions: list[str]
    calendar_status: str
    upcoming_events: list[EconEvent]
    results: list[InstrumentResult]
    universe_size: int

    @property
    def ranked(self) -> list[InstrumentResult]:
        return sorted((r for r in self.results if r.ok), key=lambda r: r.score.total, reverse=True)

    @property
    def excluded(self) -> list[InstrumentResult]:
        return [r for r in self.results if not r.ok]
