"""Live data from a running MetaTrader 5 terminal (Bybit TradFi / CFD account).

The official `MetaTrader5` Python package works on Windows only and talks to the local
terminal, so every number here comes from your broker's live feed.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from fnmatch import fnmatch
from statistics import median, quantiles

from .classify import classify
from .config import Settings
from .models import AccountInfo, Bar, MarketData, Quote, SymbolSpec, TickStats

SWAP_MODES = {
    0: "disabled", 1: "points", 2: "symbol currency", 3: "margin currency", 4: "deposit currency",
    5: "interest (current price)", 6: "interest (open price)", 7: "reopen (current)", 8: "reopen (bid)",
}
ACCOUNT_MODES = {0: "DEMO", 1: "CONTEST", 2: "REAL"}
TRADE_MODE_FULL = 4
AUTO_OFFSET_RANGE_H = (-2.0, 4.0)  # typical broker server-time offsets; outside -> ask for config


class ProviderError(RuntimeError):
    pass


class MT5Provider:
    def __init__(self, settings: Settings):
        try:
            import MetaTrader5 as mt5  # noqa: N813
        except ImportError as exc:
            raise ProviderError(
                "Пакет MetaTrader5 не установлен. Он работает только на Windows: "
                "`pip install MetaTrader5`, затем запустите терминал MT5 и войдите в Bybit-аккаунт."
            ) from exc
        self.mt5 = mt5
        self.s = settings
        self.offset_sec = 0.0
        self.offset_note = ""

    # ---------- connection ----------
    def connect(self) -> None:
        kw = {}
        if self.s.mt5_path:
            kw["path"] = self.s.mt5_path
        if self.s.mt5_login:
            kw.update(login=int(self.s.mt5_login), password=self.s.mt5_password, server=self.s.mt5_server)
        if not self.mt5.initialize(**kw):
            raise ProviderError(f"MT5 initialize() failed: {self.mt5.last_error()}")
        if self.mt5.account_info() is None:
            raise ProviderError("MT5 запущен, но аккаунт не авторизован (account_info() = None)")
        self.refresh()

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def refresh(self) -> None:
        """MT5 timestamps are broker server time. Detect its UTC offset from the freshest quote."""
        if self.s.server_utc_offset_hours is not None:
            self.offset_sec = float(self.s.server_utc_offset_hours) * 3600
            self.offset_note = "from config"
            return
        infos = self.mt5.symbols_get() or ()
        latest = max((i.time for i in infos if i.time), default=0)
        if not latest:
            self.offset_sec, self.offset_note = 0.0, "unknown (no quotes) — assumed 0"
            return
        raw_h = (latest - _time.time()) / 3600
        hours = round(raw_h * 2) / 2
        lo, hi = AUTO_OFFSET_RANGE_H
        if lo <= hours <= hi:
            self.offset_sec, self.offset_note = hours * 3600, "auto-detected from latest quote"
        else:
            self.offset_sec = 0.0
            self.offset_note = (f"auto-detect failed ({raw_h:+.1f} h, market likely closed) — assumed 0; "
                                "set server_utc_offset_hours in config.toml")

    stale_note = None

    def calendar(self):
        return None  # the Python API has no calendar access -> ForexFactory feed is used

    @property
    def offset_hours(self) -> float:
        return self.offset_sec / 3600

    def to_utc(self, server_ts: float) -> datetime:
        return datetime.fromtimestamp(server_ts - self.offset_sec, timezone.utc)

    # ---------- metadata ----------
    def account(self) -> AccountInfo:
        a = self.mt5.account_info()
        return AccountInfo(
            login=a.login, server=a.server, company=a.company, currency=a.currency,
            leverage=a.leverage, balance=a.balance, free_margin=a.margin_free,
            mode=ACCOUNT_MODES.get(a.trade_mode, str(a.trade_mode)),
        )

    def _spec(self, i) -> SymbolSpec:
        return SymbolSpec(
            symbol=i.name, asset_class=classify(i.name, i.path, i.description),
            description=i.description, path=i.path, digits=i.digits, point=i.point,
            tick_size=i.trade_tick_size, tick_value_loss=i.trade_tick_value_loss or i.trade_tick_value,
            tick_value_profit=i.trade_tick_value_profit or i.trade_tick_value,
            contract_size=i.trade_contract_size, volume_min=i.volume_min, volume_step=i.volume_step,
            volume_max=i.volume_max, currency_base=i.currency_base, currency_profit=i.currency_profit,
            currency_margin=i.currency_margin, swap_long=i.swap_long, swap_short=i.swap_short,
            swap_mode=SWAP_MODES.get(i.swap_mode, str(i.swap_mode)),
            trade_enabled=i.trade_mode == TRADE_MODE_FULL,
        )

    def symbols(self) -> list[SymbolSpec]:
        infos = self.mt5.symbols_get(group=self.s.symbol_group) or ()
        return [
            self._spec(i) for i in infos
            if not any(fnmatch(i.name, pat) for pat in self.s.exclude_symbols)
        ]

    def resolve(self, name: str) -> str | None:
        """Exact name, then case-insensitive, then '<name>.' prefix (e.g. USOUSD -> USOUSD.s)."""
        names = [i.name for i in (self.mt5.symbols_get() or ())]
        if name in names:
            return name
        low = {n.lower(): n for n in names}
        if name.lower() in low:
            return low[name.lower()]
        pref = [n for n in names if n.lower().startswith(name.lower().rstrip(".") + ".")]
        return pref[0] if len(pref) == 1 else None

    # ---------- market data ----------
    def _bars(self, name: str, tf, count: int) -> list[Bar]:
        rates = self.mt5.copy_rates_from_pos(name, tf, 0, count)
        if rates is None or len(rates) < count // 2:
            _time.sleep(0.3)  # freshly selected symbols need a moment to sync history
            rates = self.mt5.copy_rates_from_pos(name, tf, 0, count)
        if rates is None:
            return []
        return [
            Bar(self.to_utc(int(r["time"])), float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), int(r["tick_volume"]), int(r["spread"]))
            for r in rates
        ]

    def _tick_stats(self, name: str, last_server_ts: int) -> TickStats | None:
        window = self.s.tick_window_sec
        ticks = self.mt5.copy_ticks_from(name, int(last_server_ts) - window, 200_000,
                                         self.mt5.COPY_TICKS_INFO)
        if ticks is None or len(ticks) == 0:
            return None
        spreads = [float(t["ask"] - t["bid"]) for t in ticks if t["ask"] > 0 and t["bid"] > 0]
        if not spreads:
            return None
        p90 = quantiles(spreads, n=10)[-1] if len(spreads) >= 10 else max(spreads)
        return TickStats(window, len(ticks), median(spreads), p90, max(spreads))

    def snapshot(self, spec: SymbolSpec) -> MarketData:
        mt5, s = self.mt5, self.s
        info = mt5.symbol_info(spec.symbol)
        selected_here = False
        if info is not None and not info.visible:
            selected_here = mt5.symbol_select(spec.symbol, True)
            info = mt5.symbol_info(spec.symbol)
        try:
            spec = self._spec(info) if info is not None else spec
            tick = mt5.symbol_info_tick(spec.symbol)
            quote = None
            if tick is not None and tick.bid > 0 and tick.ask > 0:
                quote = Quote(tick.bid, tick.ask, self.to_utc(tick.time_msc / 1000))
            longest = max(s.range_bars_m5, s.range_bars_m15, s.atr_period * 3) + 2
            md = MarketData(
                spec=spec, quote=quote,
                bars_m1=self._bars(spec.symbol, mt5.TIMEFRAME_M1,
                                   max(s.baseline_bars_m1, s.range_bars_m1, s.atr_period * 3) + 2),
                bars_m5=self._bars(spec.symbol, mt5.TIMEFRAME_M5, longest),
                bars_m15=self._bars(spec.symbol, mt5.TIMEFRAME_M15, longest),
                tick_stats=self._tick_stats(spec.symbol, tick.time) if tick is not None else None,
                margin_min_lot=None,
                fetched_at_utc=datetime.now(timezone.utc),
            )
            if quote is not None and spec.volume_min > 0:
                margins = [
                    mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, spec.symbol, spec.volume_min, quote.ask),
                    mt5.order_calc_margin(mt5.ORDER_TYPE_SELL, spec.symbol, spec.volume_min, quote.bid),
                ]
                margins = [m for m in margins if m is not None and m > 0]
                md.margin_min_lot = max(margins) if margins else None
            return md
        finally:
            if selected_here:
                mt5.symbol_select(spec.symbol, False)
