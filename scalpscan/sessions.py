"""Trading sessions (DST-aware via zoneinfo) and per-instrument session fit."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from .classify import INDEX_TOKENS, base_name

# name -> (tz, local open, local close)
SESSIONS = {
    "Sydney": ("Australia/Sydney", time(7, 0), time(16, 0)),
    "Tokyo": ("Asia/Tokyo", time(9, 0), time(18, 0)),
    "London": ("Europe/London", time(8, 0), time(17, 0)),
    "New York": ("America/New_York", time(8, 0), time(17, 0)),
}
US_CASH = ("America/New_York", time(9, 30), time(16, 0))

CCY_HOME = {
    "JPY": "Tokyo", "AUD": "Sydney", "NZD": "Sydney", "CNH": "Tokyo", "HKD": "Tokyo", "SGD": "Tokyo",
    "EUR": "London", "GBP": "London", "CHF": "London", "SEK": "London", "NOK": "London",
    "PLN": "London", "HUF": "London", "CZK": "London", "ZAR": "London", "TRY": "London",
    "USD": "New York", "CAD": "New York", "MXN": "New York",
}


def _in_window(now_utc: datetime, tz: str, start: time, end: time) -> bool:
    local = now_utc.astimezone(ZoneInfo(tz))
    if local.weekday() >= 5:
        return False
    return start <= local.time() < end


def fx_weekend(now_utc: datetime) -> bool:
    """FX week: Sunday 17:00 New York -> Friday 17:00 New York."""
    ny = now_utc.astimezone(ZoneInfo("America/New_York"))
    wd, t = ny.weekday(), ny.time()
    return wd == 5 or (wd == 4 and t >= time(17)) or (wd == 6 and t < time(17))


def active_sessions(now_utc: datetime) -> list[str]:
    if fx_weekend(now_utc):
        return []
    return [name for name, (tz, a, b) in SESSIONS.items() if _in_window(now_utc, tz, a, b)]


def us_cash_open(now_utc: datetime) -> bool:
    return _in_window(now_utc, *US_CASH)


def session_label(now_utc: datetime) -> str:
    act = active_sessions(now_utc)
    if not act:
        return "Weekend / FX market closed"
    if {"London", "New York"} <= set(act):
        return "London + New York overlap"
    if {"Tokyo", "London"} <= set(act):
        return "Tokyo + London overlap"
    return " + ".join(act)


def home_sessions(symbol: str, asset_class: str, currency_profit: str) -> set[str]:
    name = base_name(symbol)
    if asset_class == "Forex" and len(name) >= 6:
        return {CCY_HOME.get(name[:3], "London"), CCY_HOME.get(name[3:6], "London")}
    if asset_class == "Index":
        for token, ccy in INDEX_TOKENS.items():
            if name.startswith(token):
                return {"US cash"} if ccy == "USD" else {CCY_HOME.get(ccy, "London")}
    if asset_class == "Commodities":
        if name.startswith(("XAU", "XAG", "XPT", "XPD", "GOLD", "SILVER")):
            return {"London", "New York"}
        return {"New York"}  # energy
    if asset_class == "Stocks":
        home = CCY_HOME.get(currency_profit or "USD", "New York")
        return {"US cash"} if home == "New York" else {home}
    if asset_class == "Crypto":
        return {"24/7"}
    return set()


def session_fit(symbol: str, asset_class: str, currency_profit: str,
                now_utc: datetime) -> tuple[bool | None, str]:
    homes = home_sessions(symbol, asset_class, currency_profit)
    if not homes:
        return None, "основная сессия не определена"
    if homes == {"24/7"}:
        return True, "торгуется 24/7"
    act = set(active_sessions(now_utc))
    if us_cash_open(now_utc):
        act.add("US cash")
    hit = homes & act
    label = ", ".join(sorted(homes))
    if hit:
        return True, f"основная сессия активна ({', '.join(sorted(hit))})"
    return False, f"вне основной сессии ({label}) — обычно ниже активность, шире спред"
