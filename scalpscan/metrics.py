"""Cost, movement, margin and Scalping Efficiency Score for one instrument.

Every number is derived from MarketData fetched from the terminal; nothing is estimated
from outside sources. Missing inputs are recorded in `data_issues` instead of being filled.
"""
from __future__ import annotations

import math
from datetime import datetime

from .calendar import events_near
from .classify import exposure_currencies
from .config import Settings
from .indicators import atr, avg_range, median_bar_spread_points, median_range
from .models import EconEvent, InstrumentResult, MarketData
from .sessions import session_fit


def lin(x: float | None, good: float, bad: float) -> float:
    """1.0 at `good`, 0.0 at `bad`, linear and clamped in between (works in both directions)."""
    if x is None or good == bad:
        return 0.0
    return max(0.0, min(1.0, (x - bad) / (good - bad)))


def margin_fit(pct: float | None, s: Settings) -> str:
    if pct is None:
        return "n/a"
    if pct <= s.margin_ok_pct:
        return "подходит"
    if pct <= s.margin_conditional_pct:
        return "условно подходит"
    return "не подходит"


def evaluate(md: MarketData, s: Settings, now_utc: datetime,
             events: list[EconEvent]) -> InstrumentResult:
    spec = md.spec
    r = InstrumentResult(spec=spec, data=md, lot=spec.volume_min)
    issue, exclude = r.data_issues.append, r.excluded_reasons.append

    if not spec.trade_enabled:
        exclude("торговля по символу отключена брокером")

    # --- quote & freshness ---
    q = md.quote
    if q is None or q.bid <= 0 or q.ask <= 0:
        issue("LIVE DATA UNAVAILABLE: нет котировки bid/ask")
        exclude("LIVE DATA UNAVAILABLE")
    else:
        r.bid, r.ask, r.mid = q.bid, q.ask, (q.bid + q.ask) / 2
        r.quote_age_sec = (now_utc - q.time_utc).total_seconds()
        if r.quote_age_sec > s.max_quote_age_sec:
            issue(f"LIVE DATA UNAVAILABLE: последняя котировка {r.quote_age_sec:.0f} с назад")
            exclude(f"LIVE DATA UNAVAILABLE (котировка старше {s.max_quote_age_sec:.0f} с — рынок закрыт/нет тиков)")
        r.spread_price = q.ask - q.bid
        r.spread_points = r.spread_price / spec.point if spec.point else None
        r.spread_ticks = r.spread_price / spec.tick_size if spec.tick_size else None

    # --- money per tick at min lot ---
    if spec.tick_size > 0 and spec.tick_value_loss > 0 and spec.volume_min > 0:
        r.money_per_tick = spec.tick_value_loss * r.lot
        r.money_per_tick_profit = (spec.tick_value_profit or spec.tick_value_loss) * r.lot
    else:
        issue("tick value / tick size / min lot недоступны")
        exclude("нельзя посчитать стоимость тика")

    # --- costs ---
    r.commission_rt = s.commission_for(spec.symbol) * r.lot * 2
    if r.money_per_tick and r.spread_ticks is not None:
        r.spread_cost = r.spread_ticks * r.money_per_tick
        r.rt_cost = r.spread_cost + r.commission_rt
        r.breakeven_ticks = r.rt_cost / r.money_per_tick
        r.rt_cost_price = r.breakeven_ticks * spec.tick_size

    # --- volatility ---
    if min(len(md.bars_m1), len(md.bars_m5)) < s.min_bars:
        issue("недостаточно истории баров 1m/5m")
        exclude("нет данных о волатильности")
    r.atr_m1 = atr(md.bars_m1, s.atr_period)
    r.atr_m5 = atr(md.bars_m5, s.atr_period)
    r.atr_m15 = atr(md.bars_m15, s.atr_period)
    r.range_m1 = avg_range(md.bars_m1, s.range_bars_m1)
    r.range_m5 = avg_range(md.bars_m5, s.range_bars_m5)
    r.range_m15 = avg_range(md.bars_m15, s.range_bars_m15)
    per_price = (r.money_per_tick / spec.tick_size) if r.money_per_tick else None
    if per_price:
        if r.range_m1:
            r.range_m1_money = r.range_m1 * per_price
        if r.range_m5:
            r.range_m5_money = r.range_m5 * per_price
    if r.rt_cost_price is not None:
        if r.range_m1:
            r.cost_ratio_m1 = r.rt_cost_price / r.range_m1
        if r.range_m5:
            r.cost_ratio_m5 = r.rt_cost_price / r.range_m5
        if r.range_m15:
            r.cost_ratio_m15 = r.rt_cost_price / r.range_m15
    base = median_range(md.bars_m1, s.baseline_bars_m1)
    if r.range_m1 and base:
        r.rel_activity = r.range_m1 / base

    # --- liquidity / spread stability (slippage proxy) ---
    ts = md.tick_stats
    if ts and ts.window_sec > 0:
        r.tick_rate_per_min = ts.tick_count / (ts.window_sec / 60)
    else:
        issue("тиковая история недоступна (ликвидность оценена только по барам)")
    if r.spread_price is not None:
        if ts and ts.spread_median > 0:
            r.spread_vs_median = r.spread_price / ts.spread_median
            r.spread_p90_vs_median = ts.spread_p90 / ts.spread_median
        else:
            med_pts = median_bar_spread_points(md.bars_m1, s.baseline_bars_m1)
            if med_pts and spec.point:
                r.spread_vs_median = r.spread_price / (med_pts * spec.point)

    # --- margin & risk for the $ deposit ---
    if md.margin_min_lot is None or md.margin_min_lot <= 0:
        issue("MARGIN UNAVAILABLE: терминал не вернул маржу")
        exclude("маржа недоступна")
    else:
        r.margin = md.margin_min_lot
        r.margin_pct = r.margin / s.deposit * 100
    r.margin_fit = margin_fit(r.margin_pct, s)
    if per_price and r.mid:
        r.notional = r.mid * per_price
        if r.margin:
            r.effective_leverage = r.notional / r.margin
    if per_price and r.atr_m5:
        r.stop_atr5_money = r.atr_m5 * per_price
        r.stop_atr5_pct_deposit = r.stop_atr5_money / s.deposit * 100

    # --- context ---
    r.home_session_active, r.sessions_note = session_fit(
        spec.symbol, spec.asset_class, spec.currency_profit, now_utc)
    ccys = exposure_currencies(spec.symbol, spec.asset_class, spec.currency_profit, spec.currency_base)
    r.events = events_near(events, ccys, now_utc, s.event_lookahead_min, s.event_lookback_min)
    r.high_event_risk = bool(r.events)

    # --- filters ---
    if r.margin_pct is not None and r.margin_pct > s.margin_conditional_pct:
        exclude(f"маржа мин. позиции {r.margin:.2f} = {r.margin_pct:.0f}% депозита (> {s.margin_conditional_pct:.0f}%)")
    if r.cost_ratio_m1 is not None and r.cost_ratio_m1 > s.max_cost_ratio_m1:
        exclude(f"round-trip cost = {r.cost_ratio_m1:.0%} средней 1m свечи")
    if r.cost_ratio_m5 is not None and r.cost_ratio_m5 > s.max_cost_ratio_m5:
        exclude(f"round-trip cost = {r.cost_ratio_m5:.0%} средней 5m свечи")
    if r.spread_vs_median is not None and r.spread_vs_median > s.max_spread_vs_median:
        exclude(f"спред расширен ×{r.spread_vs_median:.1f} к медиане")
    if r.tick_rate_per_min is not None and r.tick_rate_per_min < s.min_tick_rate_per_min:
        exclude(f"низкая активность: {r.tick_rate_per_min:.1f} тиков/мин")
    if r.stop_atr5_pct_deposit is not None and r.stop_atr5_pct_deposit > s.max_stop_atr5_pct:
        exclude(f"стоп 1×ATR(5m) на мин. лоте = {r.stop_atr5_pct_deposit:.0f}% депозита")

    r.ok = not r.excluded_reasons
    score(r, s)
    return r


def score(r: InstrumentResult, s: Settings) -> None:
    c: dict[str, float] = {}
    c["cost_m5"] = lin(r.cost_ratio_m5, 0.05, 0.40)
    c["cost_m1"] = lin(r.cost_ratio_m1, 0.15, 1.00)
    c["margin"] = lin(r.margin_pct, 5.0, s.margin_conditional_pct)
    c["risk_fit"] = lin(r.stop_atr5_pct_deposit, s.risk_per_trade_pct, 3 * s.risk_per_trade_pct)
    if r.rel_activity is None:
        c["activity"] = 0.0
    else:
        # Enough movement is rewarded, more than normal is NOT; a spike (news, gap) is penalised.
        spike = 1.0 if r.rel_activity <= 2.5 else max(0.5, 1 - (r.rel_activity - 2.5) * 0.25)
        c["activity"] = lin(r.rel_activity, 0.9, 0.3) * spike
    if r.spread_vs_median is None:
        c["spread_stability"] = 0.5  # unknown -> neutral, reported in data_issues
    else:
        parts = [lin(r.spread_vs_median, 1.1, 3.0)]
        if r.spread_p90_vs_median is not None:
            parts.append(lin(r.spread_p90_vs_median, 1.3, 3.0))
        c["spread_stability"] = sum(parts) / len(parts)
    if r.tick_rate_per_min:
        c["liquidity"] = lin(math.log10(r.tick_rate_per_min), math.log10(120), math.log10(5))
    else:
        c["liquidity"] = 0.0

    sb = r.score
    sb.components = c
    sb.weighted = {k: c[k] * s.weights.get(k, 0.0) for k in c}
    sb.event_factor = s.event_score_factor if r.high_event_risk else 1.0
    total = sum(sb.weighted.values()) / (sum(s.weights.values()) or 1) * 100
    sb.total = round(total * sb.event_factor, 1)
