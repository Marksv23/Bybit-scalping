"""Command interface: SCAN, SCAN FOREX|INDEX|COMMODITIES|STOCKS|CRYPTO, COMPARE A B, WHY X, REFRESH."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .report import render_compare, render_scan, render_why
from .scanner import Scanner

CLASS_ALIASES = {
    "FOREX": "Forex", "FX": "Forex", "INDEX": "Index", "INDICES": "Index",
    "COMMODITIES": "Commodities", "COMMODITY": "Commodities", "STOCKS": "Stocks",
    "STOCK": "Stocks", "CRYPTO": "Crypto",
}
HELP = """Команды:
  SCAN                      полный анализ рынка прямо сейчас
  SCAN FOREX|INDEX|COMMODITIES|STOCKS|CRYPTO
  COMPARE USOUSD.s AUDJPY.s подробное сравнение двух инструментов
  WHY SYMBOL                почему инструмент получил текущий score
  REFRESH                   заново получить данные и пересчитать последнюю команду
  HELP / EXIT"""


@dataclass
class Command:
    kind: str  # scan / compare / why / refresh / help / exit
    asset_class: str | None = None
    symbols: tuple[str, ...] = ()
    text: str = ""


def parse(line: str) -> Command:
    parts = line.strip().split()
    if not parts:
        raise ValueError("пустая команда")
    head, rest = parts[0].upper(), parts[1:]
    text = " ".join([head] + rest)
    if head == "SCAN":
        if not rest:
            return Command("scan", text="SCAN")
        cls = CLASS_ALIASES.get(rest[0].upper())
        if cls is None or len(rest) > 1:
            raise ValueError(f"неизвестный класс активов: {' '.join(rest)}")
        return Command("scan", asset_class=cls, text=f"SCAN {rest[0].upper()}")
    if head == "COMPARE":
        if len(rest) != 2:
            raise ValueError("COMPARE требует ровно два символа")
        return Command("compare", symbols=tuple(rest), text=text)
    if head == "WHY":
        if len(rest) != 1:
            raise ValueError("WHY требует один символ")
        return Command("why", symbols=tuple(rest), text=text)
    if head in {"REFRESH", "HELP", "EXIT", "QUIT"}:
        return Command({"QUIT": "exit"}.get(head, head.lower()), text=head)
    raise ValueError(f"неизвестная команда: {parts[0]}")


class App:
    def __init__(self, scanner: Scanner, settings: Settings, save_dir: Path | None = None):
        self.scanner = scanner
        self.s = settings
        self.save_dir = save_dir
        self.last: Command | None = None

    def execute(self, cmd: Command) -> str:
        if cmd.kind == "help":
            return HELP
        if cmd.kind == "refresh":
            if self.last is None:
                return "Нечего обновлять — сначала выполните SCAN / COMPARE / WHY."
            cmd = self.last
        self.last = cmd

        if cmd.kind == "scan":
            res = self.scanner.run(cmd.text, asset_class=cmd.asset_class, progress=_progress)
            out = render_scan(res, self.s)
        else:
            found, missing = self.scanner.resolve_all(list(cmd.symbols))
            if missing:
                return f"Символ(ы) не найдены в терминале: {', '.join(missing)}"
            res = self.scanner.run(cmd.text, only=found)
            by_name = {r.symbol: r for r in res.results}
            missing = [n for n in found if n not in by_name]
            if missing:
                return (f"Символ(ы) {', '.join(missing)} есть в терминале, но не входят в symbol_group "
                        f"'{self.s.symbol_group}' или исключены в config")
            res.results = [by_name[n] for n in found]
            out = render_compare(res, self.s) if cmd.kind == "compare" else render_why(res, self.s)
        self._save(cmd, out)
        return out

    def _save(self, cmd: Command, text: str) -> None:
        if not self.save_dir:
            return
        self.save_dir.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{cmd.text.replace(' ', '_')}.md"
        (self.save_dir / name).write_text(text, encoding="utf-8")


def _progress(n: int, total: int, symbol: str) -> None:
    print(f"\r  fetching {n}/{total} {symbol:<20}", end="" if n < total else "\n", file=sys.stderr, flush=True)


def make_provider(settings: Settings):
    from .provider_file import FileProvider
    from .provider_mt5 import MT5Provider

    source = settings.source
    if source == "auto":
        source = "file" if settings.snapshot_path or sys.platform != "win32" else "mt5"
    return FileProvider(settings) if source == "file" else MT5Provider(settings)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scalpscan", description="Bybit CFD scalping efficiency scanner (MT5)")
    ap.add_argument("command", nargs="*", help="e.g. SCAN, SCAN FOREX, COMPARE USOUSD.s AUDJPY.s, WHY XAUUSD.s")
    ap.add_argument("--config", help="path to config.toml (default: ./config.toml if present)")
    ap.add_argument("--save", metavar="DIR", help="also save every report as Markdown into DIR")
    ap.add_argument("--source", choices=["auto", "mt5", "file"],
                    help="mt5 = MetaTrader5 Python API (Windows); file = ScalpScanExporter snapshot (macOS/Linux)")
    ap.add_argument("--snapshot", metavar="PATH", help="path to snapshot.json written by ScalpScanExporter")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    settings = Settings.load(args.config)
    if args.source:
        settings.source = args.source
    if args.snapshot:
        settings.snapshot_path = args.snapshot

    from .provider_mt5 import ProviderError

    try:
        provider = make_provider(settings)
        provider.connect()
    except ProviderError as exc:
        print(f"LIVE DATA UNAVAILABLE: {exc}", file=sys.stderr)
        return 2

    app = App(Scanner(provider, settings), settings, Path(args.save) if args.save else None)
    try:
        if args.command:
            print(app.execute(parse(" ".join(args.command))))
            return 0
        print(HELP)
        while True:
            try:
                line = input("\nscalpscan> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not line.strip():
                continue
            try:
                cmd = parse(line)
            except ValueError as exc:
                print(f"Ошибка: {exc}\n{HELP}")
                continue
            if cmd.kind == "exit":
                break
            print(app.execute(cmd))
        return 0
    finally:
        provider.shutdown()
