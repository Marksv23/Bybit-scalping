"""Asset-class detection and currency exposure (for the economic calendar)."""
from __future__ import annotations

import re

CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "PLN",
    "HUF", "CZK", "TRY", "ZAR", "MXN", "SGD", "HKD", "CNH", "CNY",
}
COMMODITY_TOKENS = (
    "XAU", "XAG", "XPT", "XPD", "USO", "UKO", "WTI", "BRENT", "OIL", "NGAS", "NATGAS",
    "COPPER", "XCU", "GOLD", "SILVER",
)
CRYPTO_TOKENS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LTC", "BNB", "DOT", "AVAX", "LINK")
# index token -> currency whose news moves it
INDEX_TOKENS = {
    "US30": "USD", "DJ30": "USD", "DJI": "USD", "US500": "USD", "SPX": "USD", "SP500": "USD",
    "US100": "USD", "NAS": "USD", "NDX": "USD", "USTEC": "USD", "US2000": "USD", "RUS": "USD",
    "VIX": "USD", "DXY": "USD", "USDX": "USD",
    "GER": "EUR", "DE30": "EUR", "DE40": "EUR", "DAX": "EUR", "EU50": "EUR", "STOXX": "EUR",
    "FRA": "EUR", "F40": "EUR", "CAC": "EUR", "ESP": "EUR", "SPA35": "EUR", "IT40": "EUR", "NL25": "EUR",
    "UK100": "GBP", "FTSE": "GBP",
    "JP225": "JPY", "JPN225": "JPY", "NIKKEI": "JPY", "N225": "JPY",
    "HK50": "HKD", "HSI": "HKD", "CHINA": "CNY", "CN50": "CNY", "CHINA50": "CNY", "A50": "CNY",
    "AUS200": "AUD", "ASX": "AUD", "SWI20": "CHF", "SMI": "CHF",
}


def base_name(symbol: str) -> str:
    """Strip broker suffixes like '.s', '.pro', '-ECN' and non-alphanumerics."""
    name = symbol.upper().split(".")[0].split("-")[0].split("_")[0]
    return re.sub(r"[^A-Z0-9]", "", name)


def classify(symbol: str, path: str = "", description: str = "") -> str:
    p = path.lower()
    if any(k in p for k in ("forex", "fx\\", "fx/", "currenc")):
        return "Forex"
    if any(k in p for k in ("indic", "index", "indexes")):
        return "Index"
    if any(k in p for k in ("metal", "commod", "energ", "oil", "gas")):
        return "Commodities"
    if any(k in p for k in ("stock", "share", "equit")):
        return "Stocks"
    if "crypto" in p:
        return "Crypto"

    name = base_name(symbol)
    if any(name.startswith(t) for t in COMMODITY_TOKENS):
        return "Commodities"
    if any(name.startswith(t) for t in CRYPTO_TOKENS):
        return "Crypto"
    if any(name.startswith(t) for t in INDEX_TOKENS):
        return "Index"
    if len(name) == 6 and name[:3] in CURRENCIES and name[3:] in CURRENCIES:
        return "Forex"
    if name.isalpha() and 1 <= len(name) <= 5:
        return "Stocks"
    return "Other"


def exposure_currencies(symbol: str, asset_class: str, currency_profit: str,
                        currency_base: str = "") -> set[str]:
    name = base_name(symbol)
    out: set[str] = set()
    if asset_class == "Forex" and len(name) >= 6:
        out |= {name[:3], name[3:6]}
    elif asset_class == "Index":
        for token, ccy in INDEX_TOKENS.items():
            if name.startswith(token):
                out.add(ccy)
                break
    elif asset_class == "Commodities":
        out.add("USD")  # metals & energy are USD-priced; US data moves them most
    elif asset_class == "Stocks":
        out.add(currency_profit or "USD")
    for ccy in (currency_profit, currency_base):
        if ccy in CURRENCIES:
            out.add(ccy)
    out.discard("CNH")
    return out
