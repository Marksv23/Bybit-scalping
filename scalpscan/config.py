"""Settings with defaults; optionally overridden by a TOML file (see config.example.toml)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Settings:
    # --- account / risk ---
    deposit: float = 30.0
    risk_per_trade_pct: float = 2.0  # % of deposit you accept losing on one stop
    # Commission per 1.0 lot per side in account currency. MT5 does not expose commission
    # through its API, so it must be set here. Zero-Fee CFD accounts -> 0.
    commission_per_lot_side: float = 0.0
    commission_overrides: dict[str, float] = field(default_factory=dict)  # symbol -> per lot per side

    # --- data source: "auto" (MT5 Python API on Windows, else snapshot file), "mt5" or "file" ---
    source: str = "auto"
    snapshot_path: str = ""  # ScalpScanExporter snapshot.json; empty = auto-discover

    # --- MT5 connection (empty = attach to the running, logged-in terminal) ---
    mt5_path: str = ""
    mt5_login: int = 0
    mt5_password: str = ""
    mt5_server: str = ""
    symbol_group: str = "*"  # MT5 group filter, e.g. "*.s" for Bybit TradFi CFDs
    exclude_symbols: list[str] = field(default_factory=list)
    server_utc_offset_hours: float | None = None  # None = auto-detect from last quote times

    # --- data windows ---
    atr_period: int = 14
    range_bars_m1: int = 30  # average candle range over the last N bars
    range_bars_m5: int = 24
    range_bars_m15: int = 16
    baseline_bars_m1: int = 240  # 4h baseline for "current activity"
    tick_window_sec: int = 300

    # --- filters ---
    max_quote_age_sec: float = 120.0
    margin_ok_pct: float = 15.0  # <= -> "подходит"
    margin_conditional_pct: float = 35.0  # <= -> "условно подходит", above -> "не подходит" (excluded)
    max_stop_atr5_pct: float = 20.0  # exclude if a 1x ATR(5m) stop at min lot costs more (% of deposit)
    max_cost_ratio_m1: float = 1.0  # round-trip cost / avg 1m range
    max_cost_ratio_m5: float = 0.5  # round-trip cost / avg 5m range
    max_spread_vs_median: float = 3.0  # current spread vs recent median (abnormal widening)
    min_tick_rate_per_min: float = 5.0
    min_bars: int = 30

    # --- calendar ---
    calendar_enabled: bool = True
    calendar_url: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    calendar_cache_min: int = 15
    event_lookahead_min: int = 30  # flag HIGH EVENT RISK if a high-impact event is this close
    event_lookback_min: int = 10  # ... or happened this recently
    event_score_factor: float = 0.85

    # --- score weights (sum = 100) ---
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "cost_m5": 25.0,
            "cost_m1": 15.0,
            "margin": 15.0,
            "risk_fit": 10.0,
            "activity": 15.0,
            "spread_stability": 10.0,
            "liquidity": 10.0,
        }
    )

    @classmethod
    def load(cls, path: str | Path | None) -> "Settings":
        s = cls()
        if path is None:
            default = Path("config.toml")
            if not default.exists():
                return s
            path = default
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        flat: dict = {}
        for key, value in data.items():
            # allow [sections] purely for readability
            if isinstance(value, dict) and key not in {"weights", "commission_overrides"}:
                flat.update(value)
            else:
                flat[key] = value
        known = {f.name for f in fields(cls)}
        unknown = set(flat) - known
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        for key, value in flat.items():
            if key == "weights":
                s.weights.update(value)
            else:
                setattr(s, key, value)
        return s

    def commission_for(self, symbol: str) -> float:
        return self.commission_overrides.get(symbol, self.commission_per_lot_side)
