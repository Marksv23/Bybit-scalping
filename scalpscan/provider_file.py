"""Live data from the JSON snapshot written by mql5/ScalpScanExporter.mq5.

For macOS (MT5 runs inside Wine there, the Python API is unavailable) and Linux. The EA
rewrites the snapshot every few seconds; all timestamps in it are already UTC.
"""
from __future__ import annotations

import json
import time as _time
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

from .classify import classify
from .config import Settings
from .models import AccountInfo, Bar, EconEvent, MarketData, Quote, SymbolSpec, TickStats
from .provider_mt5 import ACCOUNT_MODES, SWAP_MODES, TRADE_MODE_FULL, ProviderError

REL = Path("scalpscan") / "snapshot.json"
SEARCH_ROOTS = [
    Path.home() / "Library" / "Application Support",  # macOS MT5 app (Wine bottle)
    Path.home() / ".wine",
    Path.home() / ".mt5",
]
MAX_FILE_AGE_SEC = 90


def find_snapshot() -> Path | None:
    """Newest `.../MQL5/Files/scalpscan/snapshot.json` or `.../Common/Files/...` under known roots."""
    found: list[Path] = []
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for base in root.iterdir():
            # in ~/Library/Application Support only look inside MT5 / Wine bottles
            if root.name == "Application Support" and not any(
                    k in base.name.lower() for k in ("wine", "metatrader", "mt5")):
                continue
            found += [p for p in base.rglob("snapshot.json") if p.parent.name == "scalpscan"]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _utc(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


class FileProvider:
    def __init__(self, settings: Settings, path: str | Path | None = None):
        self.s = settings
        path = path or settings.snapshot_path
        self.path = Path(path).expanduser() if path else None
        self.doc: dict = {}
        self.offset_note = "from exporter (TimeTradeServer − TimeGMT)"

    def connect(self) -> None:
        if self.path is None:
            self.path = find_snapshot()
        if self.path is None or not self.path.exists():
            raise ProviderError(
                "snapshot.json не найден. Запустите советник ScalpScanExporter в MT5 (см. README, раздел macOS) "
                "или укажите путь: --snapshot /path/to/snapshot.json")
        self.refresh()

    def shutdown(self) -> None:
        pass

    def refresh(self) -> None:
        for attempt in range(3):  # the EA may be mid-rename; retry briefly
            try:
                self.doc = json.loads(self.path.read_text(encoding="utf-8-sig"))
                break
            except (json.JSONDecodeError, OSError):
                if attempt == 2:
                    raise ProviderError(f"не удалось прочитать {self.path}")
                _time.sleep(0.5)
        if self.doc.get("schema") != 1:
            raise ProviderError(f"неизвестная версия snapshot: {self.doc.get('schema')}")

    @property
    def file_age_sec(self) -> float:
        return _time.time() - float(self.doc.get("generated_utc", 0))

    @property
    def stale_note(self) -> str | None:
        age = self.file_age_sec
        if age > MAX_FILE_AGE_SEC:
            return (f"snapshot обновлялся {age:.0f} с назад — советник ScalpScanExporter не работает "
                    "или MT5 закрыт")
        return None

    @property
    def offset_hours(self) -> float:
        return float(self.doc.get("server_offset_sec", 0)) / 3600

    def account(self) -> AccountInfo:
        a = self.doc.get("account", {})
        return AccountInfo(
            login=a.get("login"), server=a.get("server", ""), company=a.get("company", ""),
            currency=a.get("currency", "USD"), leverage=a.get("leverage"), balance=a.get("balance"),
            free_margin=a.get("free_margin"),
            mode=ACCOUNT_MODES.get(a.get("trade_mode"), str(a.get("trade_mode"))),
        )

    def calendar(self) -> tuple[list[EconEvent], str] | None:
        raw = self.doc.get("calendar")
        if raw is None:
            return None
        events = sorted((EconEvent(_utc(e["t"]), e["currency"].upper(), e["title"], "High") for e in raw),
                        key=lambda e: e.time_utc)
        gen = _utc(self.doc["generated_utc"])
        return events, f"MT5 built-in economic calendar (high importance), exported {gen:%H:%M:%S} UTC"

    def _items(self) -> dict[str, dict]:
        return {x["name"]: x for x in self.doc.get("symbols", [])}

    @staticmethod
    def _spec(x: dict) -> SymbolSpec:
        tv = x.get("tick_value") or 0.0
        return SymbolSpec(
            symbol=x["name"], asset_class=classify(x["name"], x.get("path", ""), x.get("description", "")),
            description=x.get("description", ""), path=x.get("path", ""), digits=int(x.get("digits", 5)),
            point=x.get("point") or 0.0, tick_size=x.get("tick_size") or 0.0,
            tick_value_loss=x.get("tick_value_loss") or tv, tick_value_profit=x.get("tick_value_profit") or tv,
            contract_size=x.get("contract_size") or 0.0, volume_min=x.get("volume_min") or 0.0,
            volume_step=x.get("volume_step") or 0.0, volume_max=x.get("volume_max") or 0.0,
            currency_base=x.get("currency_base", ""), currency_profit=x.get("currency_profit", ""),
            currency_margin=x.get("currency_margin", ""), swap_long=x.get("swap_long") or 0.0,
            swap_short=x.get("swap_short") or 0.0,
            swap_mode=SWAP_MODES.get(x.get("swap_mode"), str(x.get("swap_mode"))),
            trade_enabled=x.get("trade_mode") == TRADE_MODE_FULL,
        )

    def symbols(self) -> list[SymbolSpec]:
        return [self._spec(x) for x in self._items().values()
                if not any(fnmatch(x["name"], pat) for pat in self.s.exclude_symbols)
                and fnmatch(x["name"], self.s.symbol_group)]

    def resolve(self, name: str) -> str | None:
        names = list(self._items())
        if name in names:
            return name
        low = {n.lower(): n for n in names}
        if name.lower() in low:
            return low[name.lower()]
        pref = [n for n in names if n.lower().startswith(name.lower().rstrip(".") + ".")]
        return pref[0] if len(pref) == 1 else None

    @staticmethod
    def _bars(rows: list) -> list[Bar]:
        return [Bar(_utc(r[0]), r[1], r[2], r[3], r[4], int(r[5]), int(r[6])) for r in rows or []]

    def snapshot(self, spec: SymbolSpec) -> MarketData:
        x = self._items()[spec.symbol]
        quote = None
        if x.get("bid") and x.get("ask") and x.get("quote_utc_ms"):
            quote = Quote(x["bid"], x["ask"], _utc(x["quote_utc_ms"] / 1000))
        t = x.get("ticks")
        ticks = (TickStats(int(t["window_sec"]), int(t["count"]), t["spread_median"], t["spread_p90"],
                           t["spread_max"]) if t else None)
        return MarketData(
            spec=self._spec(x), quote=quote, bars_m1=self._bars(x.get("m1")),
            bars_m5=self._bars(x.get("m5")), bars_m15=self._bars(x.get("m15")), tick_stats=ticks,
            margin_min_lot=x.get("margin_min_lot"), fetched_at_utc=_utc(self.doc["generated_utc"]),
        )
