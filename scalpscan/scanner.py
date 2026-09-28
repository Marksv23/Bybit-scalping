"""Orchestrates one scan: fetch live data -> evaluate -> ScanResult."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .calendar import load_events
from .config import Settings
from .metrics import evaluate
from .models import AccountInfo, InstrumentResult, MarketData, ScanResult, SymbolSpec
from .sessions import session_label


class Provider(Protocol):
    offset_note: str

    @property
    def offset_hours(self) -> float: ...
    def refresh_offset(self) -> None: ...
    def account(self) -> AccountInfo: ...
    def symbols(self) -> list[SymbolSpec]: ...
    def resolve(self, name: str) -> str | None: ...
    def snapshot(self, spec: SymbolSpec) -> MarketData: ...


class Scanner:
    def __init__(self, provider: Provider, settings: Settings, clock=None):
        self.p = provider
        self.s = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, command: str, asset_class: str | None = None,
            only: list[str] | None = None, progress=None) -> ScanResult:
        self.p.refresh_offset()
        specs = self.p.symbols()
        if only is not None:
            wanted = set(only)
            specs = [sp for sp in specs if sp.symbol in wanted]
        elif asset_class:
            specs = [sp for sp in specs if sp.asset_class == asset_class]

        events, cal_status = load_events(self.s)
        results: list[InstrumentResult] = []
        for n, spec in enumerate(specs, 1):
            if progress:
                progress(n, len(specs), spec.symbol)
            md = self.p.snapshot(spec)
            results.append(evaluate(md, self.s, self.clock(), events))

        now = self.clock()
        upcoming = [e for e in events
                    if -self.s.event_lookback_min * 60 <= (e.time_utc - now).total_seconds() <= 3 * 3600]
        return ScanResult(
            command=command, analysis_time_utc=now, account=self.p.account(),
            server_utc_offset_hours=self.p.offset_hours, active_sessions=[session_label(now)],
            calendar_status=cal_status, upcoming_events=upcoming, results=results,
            universe_size=len(specs),
        )

    def resolve_all(self, names: list[str]) -> tuple[list[str], list[str]]:
        found, missing = [], []
        for n in names:
            r = self.p.resolve(n)
            (found if r else missing).append(r or n)
        return found, missing
