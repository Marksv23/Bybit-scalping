"""High-impact economic events (ForexFactory weekly JSON feed), cached on disk."""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .models import EconEvent

CACHE = Path.home() / ".scalpscan_cache" / "calendar.json"


def parse_events(raw: list[dict]) -> list[EconEvent]:
    events = []
    for item in raw:
        if str(item.get("impact", "")).lower() != "high":
            continue
        try:
            when = datetime.fromisoformat(item["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        events.append(EconEvent(when, str(item.get("country", "")).upper(),
                                str(item.get("title", "")), "High"))
    return sorted(events, key=lambda e: e.time_utc)


def load_events(settings: Settings) -> tuple[list[EconEvent], str]:
    """Return (events, status line). Never raises: the scan must go on without a calendar."""
    if not settings.calendar_enabled:
        return [], "CALENDAR DISABLED (config)"
    try:
        if CACHE.exists() and time.time() - CACHE.stat().st_mtime < settings.calendar_cache_min * 60:
            fetched = datetime.fromtimestamp(CACHE.stat().st_mtime, timezone.utc)
            raw = json.loads(CACHE.read_text(encoding="utf-8"))
        else:
            req = urllib.request.Request(settings.calendar_url, headers={"User-Agent": "scalpscan/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(raw), encoding="utf-8")
            fetched = datetime.now(timezone.utc)
    except Exception as exc:  # network, rate limit, bad JSON
        return [], f"CALENDAR UNAVAILABLE ({type(exc).__name__}) — event risk NOT checked"
    return parse_events(raw), f"ForexFactory high-impact feed, fetched {fetched:%Y-%m-%d %H:%M} UTC"


def events_near(events: list[EconEvent], currencies: set[str], now_utc: datetime,
                lookahead_min: int, lookback_min: int) -> list[EconEvent]:
    lo = now_utc - timedelta(minutes=lookback_min)
    hi = now_utc + timedelta(minutes=lookahead_min)
    return [e for e in events if e.currency in currencies and lo <= e.time_utc <= hi]
