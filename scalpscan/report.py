"""Markdown rendering of scan / compare / why results."""
from __future__ import annotations

from datetime import datetime

from .config import Settings
from .models import InstrumentResult, ScanResult

COMPONENT_LABELS = {
    "cost_m5": "Cost / 5m movement",
    "cost_m1": "Cost / 1m movement",
    "margin": "Margin efficiency",
    "risk_fit": "Risk fit (стоп 1×ATR5 vs риск на сделку)",
    "activity": "Текущая активность (без бонуса за сверхволатильность)",
    "spread_stability": "Стабильность спреда (прокси slippage)",
    "liquidity": "Ликвидность (тиков/мин)",
}
NA = "n/a"


# ---------- formatting helpers ----------
class Fmt:
    def __init__(self, ccy: str):
        self.ccy = ccy or "USD"

    def money(self, x: float | None, signed: bool = False) -> str:
        if x is None:
            return NA
        sign = "+" if signed and x > 0 else ("-" if x < 0 else "")
        v = abs(x)
        body = f"{v:.3f}" if v < 1 else f"{v:,.2f}"
        return f"{sign}${body}" if self.ccy == "USD" else f"{sign}{body} {self.ccy}"


def price(x: float | None, digits: int) -> str:
    return NA if x is None else f"{x:.{max(digits, 0)}f}"


def pct(x: float | None, nd: int = 0) -> str:
    return NA if x is None else f"{x * 100:.{nd}f}%"


def num(x: float | None, nd: int = 2) -> str:
    return NA if x is None else f"{x:,.{nd}f}"


def ts(dt: datetime | None) -> str:
    return NA if dt is None else dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def best_timeframe(r: InstrumentResult) -> str:
    if r.cost_ratio_m1 is not None and r.cost_ratio_m1 <= 0.25:
        return "1m"
    if r.cost_ratio_m5 is not None and r.cost_ratio_m5 <= 0.20:
        return "5m"
    return "15m"


def weakest(r: InstrumentResult) -> str:
    c = r.score.components
    return COMPONENT_LABELS[min(c, key=c.get)] if c else NA


# ---------- header ----------
def header(res: ScanResult, s: Settings) -> list[str]:
    f = Fmt(res.account.currency if res.account else "USD")
    quotes = [r.data.quote.time_utc for r in res.results if r.data.quote]
    fetched = [r.data.fetched_at_utc for r in res.results]
    live = [r for r in res.results if r.quote_age_sec is not None and r.quote_age_sec <= s.max_quote_age_sec]
    out = [f"# {res.command}", "",
           f"`Analysis time: {ts(res.analysis_time_utc)}`  "]
    if quotes:
        ages = [r.quote_age_sec for r in live]
        age_txt = f", max age of ranked quotes {max(ages):.0f} s" if ages else ""
        out.append(f"`Data freshness: quotes {min(quotes):%H:%M:%S}–{max(quotes):%H:%M:%S} UTC; "
                   f"bars/ticks fetched {min(fetched):%H:%M:%S}–{max(fetched):%H:%M:%S} UTC{age_txt}`")
    else:
        out.append("`Data freshness: LIVE DATA UNAVAILABLE`")
    out.append("")
    if res.results and not live:
        out += ["> **LIVE DATA UNAVAILABLE** — ни по одному инструменту нет свежей котировки "
                "(рынок закрыт или терминал не получает данные). Рейтинг не строится.", ""]
    a = res.account
    if a:
        out.append(f"- Source: MetaTrader 5 live feed · {a.company} · server `{a.server}` · "
                   f"{a.mode} · account currency {a.currency} · leverage 1:{a.leverage} · "
                   f"server time UTC{res.server_utc_offset_hours:+g}h")
        if a.currency != "USD":
            out.append(f"- ⚠ Валюта счёта {a.currency}: все денежные значения в {a.currency}, депозит "
                       f"из конфига ({s.deposit:g}) трактуется в той же валюте.")
    out.append(f"- Session: **{', '.join(res.active_sessions)}**")
    out.append(f"- Calendar: {res.calendar_status}")
    for e in res.upcoming_events:
        mins = (e.time_utc - res.analysis_time_utc).total_seconds() / 60
        flag = " ⚠ HIGH EVENT RISK" if -s.event_lookback_min <= mins <= s.event_lookahead_min else ""
        when = f"через {mins:.0f} мин" if mins >= 0 else f"{-mins:.0f} мин назад"
        out.append(f"  - {e.time_utc:%H:%M} UTC ({when}) **{e.currency}** {e.title}{flag}")
    out.append(f"- Assumptions: deposit {f.money(s.deposit)}; commission {f.money(s.commission_per_lot_side)} "
               "per lot per side (из config — MT5 API не отдаёт комиссию; Zero-Fee = 0, сверьте со "
               "спецификацией); slippage: данных исполнения нет → прокси = стабильность спреда; "
               "все расчёты — для минимального лота.")
    out.append(f"- Universe: {res.universe_size} symbols · passed filters {len(res.ranked)} · "
               f"excluded {len(res.excluded)}")
    out.append("")
    return out


# ---------- scan report ----------
def top_table(rows: list[InstrumentResult], f: Fmt) -> list[str]:
    out = ["| # | Instrument | Spread | Spread cost | 1m movement | 5m movement | Min margin | Cost/Movement | Score |",
           "| - | ---------- | -----: | ----------: | ----------: | ----------: | ---------: | ------------: | ----: |"]
    for i, r in enumerate(rows, 1):
        d = r.spec.digits
        ev = " ⚠" if r.high_event_risk else ""
        out.append(
            f"| {i} | **{r.symbol}** ({r.spec.asset_class}){ev} | {num(r.spread_points, 0)} pts "
            f"| {f.money(r.spread_cost)} | {price(r.range_m1, d)} ({f.money(r.range_m1_money)}) "
            f"| {price(r.range_m5, d)} ({f.money(r.range_m5_money)}) "
            f"| {f.money(r.margin)} ({num(r.margin_pct, 1)}%) "
            f"| 1m {pct(r.cost_ratio_m1)} · 5m {pct(r.cost_ratio_m5)} | **{r.score.total:.0f}** |")
    return out


def why_now(r: InstrumentResult, f: Fmt) -> str:
    bits = [
        f"round-trip {f.money(r.rt_cost)} съедает {pct(r.cost_ratio_m5)} средней 5m свечи и "
        f"{pct(r.cost_ratio_m1)} 1m свечи",
        f"маржа {f.money(r.margin)} ({num(r.margin_pct, 1)}% депозита — {r.margin_fit})",
        f"активность ×{num(r.rel_activity)} к 4h-норме, {num(r.tick_rate_per_min, 0)} тиков/мин",
        f"спред ×{num(r.spread_vs_median)} к медиане",
        f"стоп 1×ATR(5m) = {f.money(r.stop_atr5_money)} ({num(r.stop_atr5_pct_deposit, 1)}% депозита)",
        r.sessions_note,
    ]
    txt = "; ".join(bits) + f". Слабое место: {weakest(r)}."
    if r.high_event_risk:
        txt += " ⚠ HIGH EVENT RISK: " + ", ".join(f"{e.currency} {e.title} {e.time_utc:%H:%M} UTC"
                                                  for e in r.events)
    return txt


def detail_block(r: InstrumentResult, f: Fmt) -> list[str]:
    sp, d = r.spec, r.spec.digits
    units = sp.contract_size * r.lot
    rows = [
        ("Используемый размер позиции", f"{r.lot:g} lot (min; step {sp.volume_step:g}) = {units:g} units"),
        ("Необходимая маржа", f"{f.money(r.margin)} ({num(r.margin_pct, 1)}% от депозита, {r.margin_fit})"),
        ("1 tick", f"{price(sp.tick_size, d)} → {f.money(r.money_per_tick)}"),
        ("10 ticks", f"{price(sp.tick_size * 10, d)} → {f.money(r.money_per_tick and r.money_per_tick * 10)}"),
        ("Round-trip cost", f"{f.money(r.rt_cost)} (spread {f.money(r.spread_cost)} + commission "
                            f"{f.money(r.commission_rt)})"),
        ("Покрыть round-trip", f"{num(r.breakeven_ticks, 1)} ticks = {price(r.rt_cost_price, d)} "
                               f"({pct(r.rt_cost_price / r.mid if r.rt_cost_price and r.mid else None, 3)} цены)"),
    ]
    for move in (0.001, 0.0025):
        if r.mid and r.money_per_tick_profit and sp.tick_size:
            dp = r.mid * move
            gross = dp / sp.tick_size * r.money_per_tick_profit
            net = gross - (r.rt_cost or 0)
            rows.append((f"P/L при +{move * 100:g}%",
                         f"Δ {price(dp, d)} ({dp / sp.tick_size:,.0f} ticks): gross {f.money(gross, True)}, "
                         f"net после round-trip {f.money(net, True)}"))
        else:
            rows.append((f"P/L при +{move * 100:g}%", NA))
    out = [f"#### {r.symbol}", "", "| Параметр | Значение |", "| - | - |"]
    out += [f"| {k} | {v} |" for k, v in rows]
    return out + [""]


def render_scan(res: ScanResult, s: Settings) -> str:
    f = Fmt(res.account.currency if res.account else "USD")
    out = header(res, s)
    ranked = res.ranked
    top = ranked[:10]

    out.append("### TOP 10 — RIGHT NOW")
    out.append("")
    if top:
        out += top_table(top, f)
        if len(top) < 10:
            out.append(f"\nФильтры прошли только {len(top)} инструмент(ов) — остальные исключены (см. ниже).")
    else:
        out.append("Ни один инструмент не прошёл фильтры прямо сейчас.")
    out.append("")

    if top:
        out += ["### Почему сейчас подходит для скальпинга (TOP-5)", ""]
        out += [f"{i}. **{r.symbol}** — {why_now(r, f)}" for i, r in enumerate(top[:5], 1)]
        out.append("")
        best = next((r for r in ranked if r.margin_fit == "подходит" and not r.high_event_risk), top[0])
        out += ["### BEST FOR $30", "",
                f"**{best.symbol}** — Наиболее эффективный по заданным критериям прямо сейчас. "
                f"Score {best.score.total:.0f}; round-trip {f.money(best.rt_cost)} = {pct(best.cost_ratio_m5)} "
                f"5m-движения; маржа {f.money(best.margin)}; {num(best.tick_rate_per_min, 0)} тиков/мин.", ""]
        out += ["### Дополнительный анализ (TOP-3, минимальная позиция)", ""]
        for r in top[:3]:
            out += detail_block(r, f)

    out += ["### WATCH NOW", ""]
    watch = [r for r in ranked if not r.high_event_risk][:3] or ranked[:3]
    if watch:
        out += ["| Symbol | Spread | Volatility (ATR 1m / 5m) | Причина наблюдения | Timeframe |",
                "| - | -: | - | - | - |"]
        for r in watch:
            d = r.spec.digits
            out.append(f"| **{r.symbol}** | {num(r.spread_points, 0)} pts ({f.money(r.spread_cost)}) "
                       f"| {price(r.atr_m1, d)} / {price(r.atr_m5, d)} "
                       f"| cost = {pct(r.cost_ratio_m1)} 1m / {pct(r.cost_ratio_m5)} 5m движения; "
                       f"активность ×{num(r.rel_activity)}; {r.sessions_note} | {best_timeframe(r)} |")
    else:
        out.append("Нет кандидатов.")
    out.append("")

    out += ["### AVOID NOW", ""]
    avoid = sorted((r for r in res.excluded if r.data.quote is not None),
                   key=lambda r: r.tick_rate_per_min or 0, reverse=True)[:3]
    if len(avoid) < 3:
        watched = {r.symbol for r in watch}
        avoid += [r for r in reversed(ranked) if r.symbol not in watched][: 3 - len(avoid)]
    if not avoid:
        out.append("Нет кандидатов с живыми данными.")
    for r in avoid:
        reasons = r.excluded_reasons or [f"низкий score {r.score.total:.0f}; слабое место: {weakest(r)}"]
        out.append(f"- **{r.symbol}** (spread {num(r.spread_points, 0)} pts = {f.money(r.spread_cost)}, "
                   f"margin {f.money(r.margin)}) — " + "; ".join(reasons))
    out.append("")

    stale = [r.symbol for r in res.results if any("LIVE DATA UNAVAILABLE" in x for x in r.data_issues)]
    if stale:
        more = f" … +{len(stale) - 15}" if len(stale) > 15 else ""
        out += [f"**LIVE DATA UNAVAILABLE** ({len(stale)}): {', '.join(stale[:15])}{more}", ""]
    out.append("_Это не торговый сигнал: оценивается только математика издержек и движения, не направление цены._")
    return "\n".join(out)


# ---------- per-instrument views ----------
def param_rows(r: InstrumentResult, f: Fmt, s: Settings, leverage: int | None) -> list[tuple[str, str]]:
    sp, d = r.spec, r.spec.digits
    ts_ = r.data.tick_stats
    ev = "; ".join(f"⚠ HIGH EVENT RISK {e.currency} {e.title} {e.time_utc:%H:%M} UTC" for e in r.events)
    return [
        ("1. Symbol", sp.symbol),
        ("2. Asset class", sp.asset_class + (f" ({sp.path})" if sp.path else "")),
        ("3. Bid", price(r.bid, d)),
        ("4. Ask", price(r.ask, d)),
        ("5. Spread", f"{num(r.spread_points, 0)} pts = {price(r.spread_price, d)} = {num(r.spread_ticks, 1)} ticks"),
        ("6. Tick size", f"{price(sp.tick_size, d)} (point {price(sp.point, d)})"),
        ("7. Contract size", f"{sp.contract_size:g}"),
        ("8. Minimum order size", f"{sp.volume_min:g} lot (step {sp.volume_step:g}, max {sp.volume_max:g})"),
        ("9. Minimum required margin", f"{f.money(r.margin)} = {num(r.margin_pct, 1)}% депозита → {r.margin_fit}"),
        ("10. Leverage", f"account 1:{leverage if leverage else NA}; effective на мин. лоте "
                         f"{num(r.effective_leverage, 1)}x (notional {f.money(r.notional)})"),
        ("11. Trading fee", f"{f.money(r.commission_rt)} round-trip на мин. лоте (из config)"),
        ("12. Swap / overnight", f"long {sp.swap_long:g}, short {sp.swap_short:g} ({sp.swap_mode}); "
                                 "для сделок < 15 мин важно только при переносе через rollover"),
        ("13. ATR(14) 1m", price(r.atr_m1, d)),
        ("14. ATR(14) 5m", f"{price(r.atr_m5, d)} (15m: {price(r.atr_m15, d)})"),
        ("15. Avg range 1m", f"{price(r.range_m1, d)} ({f.money(r.range_m1_money)}) за {s.range_bars_m1} баров"),
        ("16. Avg range 5m", f"{price(r.range_m5, d)} ({f.money(r.range_m5_money)}) за {s.range_bars_m5} баров; "
                             f"15m: {price(r.range_m15, d)}"),
        ("17. Текущая активность", f"×{num(r.rel_activity)} к медиане 1m-диапазона за {s.baseline_bars_m1} мин"),
        ("18. Ликвидность", f"{num(r.tick_rate_per_min, 1)} тиков/мин за {s.tick_window_sec // 60} мин"
                            + (f" ({ts_.tick_count} тиков)" if ts_ else " — тики недоступны")
                            + "; объёмы CFD-фида не публикуются"),
        ("19. Slippage", "n/a — нет данных исполнения; прокси: спред сейчас "
                         f"×{num(r.spread_vs_median)} к медиане, p90/median ×{num(r.spread_p90_vs_median)}"),
        ("20. Сессия", r.sessions_note),
        ("21. События", ev or "нет high-impact событий в окне"),
        ("Spread cost (мин. лот)", f.money(r.spread_cost)),
        ("Round-trip cost", f.money(r.rt_cost)),
        ("Break-even", f"{num(r.breakeven_ticks, 1)} ticks = {price(r.rt_cost_price, d)}"),
        ("Cost / movement", f"1m {pct(r.cost_ratio_m1)} · 5m {pct(r.cost_ratio_m5)} · 15m {pct(r.cost_ratio_m15)}"),
        ("Стоп 1×ATR(5m) на мин. лоте", f"{f.money(r.stop_atr5_money)} = {num(r.stop_atr5_pct_deposit, 1)}% депозита"),
        ("Возраст котировки", f"{num(r.quote_age_sec, 0)} s"),
        ("Score", f"**{r.score.total:.0f}** / 100" + ("" if r.ok else " (исключён фильтрами)")),
    ]


def score_table(r: InstrumentResult, s: Settings) -> list[str]:
    raw = {
        "cost_m5": pct(r.cost_ratio_m5), "cost_m1": pct(r.cost_ratio_m1),
        "margin": f"{num(r.margin_pct, 1)}% депозита", "risk_fit": f"{num(r.stop_atr5_pct_deposit, 1)}% депозита",
        "activity": f"×{num(r.rel_activity)}", "spread_stability": f"×{num(r.spread_vs_median)}",
        "liquidity": f"{num(r.tick_rate_per_min, 1)}/мин",
    }
    out = ["| Компонент | Метрика | 0..1 | Вес | Баллы |", "| - | -: | -: | -: | -: |"]
    for k, v in r.score.components.items():
        out.append(f"| {COMPONENT_LABELS[k]} | {raw[k]} | {v:.2f} | {s.weights.get(k, 0):g} "
                   f"| {r.score.weighted[k]:.1f} |")
    if r.score.event_factor != 1:
        out.append(f"| ⚠ HIGH EVENT RISK | | | ×{r.score.event_factor:g} | |")
    out.append(f"| **Итого** | | | | **{r.score.total:.0f}** |")
    return out


def render_why(res: ScanResult, s: Settings) -> str:
    f = Fmt(res.account.currency if res.account else "USD")
    lev = res.account.leverage if res.account else None
    out = header(res, s)
    for r in res.results:
        out += [f"## WHY {r.symbol}", "", "| Параметр | Значение |", "| - | - |"]
        out += [f"| {k} | {v} |" for k, v in param_rows(r, f, s, lev)]
        out += ["", "**Score breakdown**", ""] + score_table(r, s) + [""]
        out.append("**Фильтры:** " + ("пройдены" if r.ok else "; ".join(r.excluded_reasons)))
        if r.data_issues:
            out.append("**Проблемы данных:** " + "; ".join(r.data_issues))
        out.append("")
    return "\n".join(out)


def render_compare(res: ScanResult, s: Settings) -> str:
    f = Fmt(res.account.currency if res.account else "USD")
    lev = res.account.leverage if res.account else None
    out = header(res, s)
    a, b = res.results
    out += [f"## COMPARE {a.symbol} vs {b.symbol}", "",
            f"| Параметр | {a.symbol} | {b.symbol} |", "| - | - | - |"]
    for (k, va), (_, vb) in zip(param_rows(a, f, s, lev), param_rows(b, f, s, lev)):
        out.append(f"| {k} | {va} | {vb} |")
    out += ["", "**Почему голый spread вводит в заблуждение:** "
            f"{a.symbol} {num(a.spread_points, 0)} pts = {f.money(a.spread_cost)} на мин. лоте "
            f"({pct(a.cost_ratio_m5)} 5m-движения) против {b.symbol} {num(b.spread_points, 0)} pts = "
            f"{f.money(b.spread_cost)} ({pct(b.cost_ratio_m5)} 5m-движения).", ""]
    for r in (a, b):
        out += [f"**{r.symbol} score**", ""] + score_table(r, s) + [""]
        if not r.ok:
            out.append("Исключён: " + "; ".join(r.excluded_reasons) + "\n")
    both = [r for r in (a, b) if r.ok]
    if len(both) == 2:
        w = max(both, key=lambda r: r.score.total)
        out.append(f"По заданным критериям прямо сейчас эффективнее: **{w.symbol}** "
                   f"({a.score.total:.0f} vs {b.score.total:.0f}).")
    elif both:
        out.append(f"Фильтры прошёл только **{both[0].symbol}**.")
    else:
        out.append("Оба инструмента сейчас не проходят фильтры.")
    return "\n".join(out)
