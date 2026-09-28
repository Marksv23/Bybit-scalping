#!/bin/bash
# macOS setup for scalpscan (bash 3.2 compatible — the macOS default shell).
#   1. finds Python 3.11+ (offers to install via Homebrew),
#   2. finds the MetaTrader 5 Wine bottle and copies ScalpScanExporter.mq5 into MQL5/Experts,
#   3. tries to compile it with MetaEditor through the app's bundled Wine,
#   4. with --run: waits for snapshot.json and runs SCAN.
# Nothing is installed or changed without asking, except copying the EA source into MT5's Experts folder.
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
EA_SRC="$REPO/mql5/ScalpScanExporter.mq5"
SUPPORT="${MT5_SUPPORT_DIR:-$HOME/Library/Application Support}"
APPS="${MT5_APPS_DIR:-/Applications}"
RUN=0
[ "${1:-}" = "--run" ] && RUN=1

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  ✓ %s\n' "$*"; }
warn() { printf '  ⚠ %s\n' "$*"; }
ask()  { printf '  ? %s [y/N] ' "$*"; read -r a; [ "$a" = "y" ] || [ "$a" = "Y" ]; }

# ---------- 1. Python ----------
say "1/4 Python 3.11+"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  warn "Python 3.11+ не найден."
  if command -v brew >/dev/null 2>&1 && ask "Установить python через Homebrew (brew install python)?"; then
    brew install python && PY="$(brew --prefix)/bin/python3"
  else
    warn "Установите Python с https://www.python.org/downloads/macos/ и запустите скрипт снова."
    exit 1
  fi
fi
ok "$PY ($("$PY" -V 2>&1))"

# ---------- 2. MT5 bottle ----------
say "2/4 MetaTrader 5"
MQL5_DIRS="$(find "$SUPPORT" -maxdepth 9 -type d -name MQL5 -path '*drive_c*' 2>/dev/null)"
if [ -z "$MQL5_DIRS" ]; then
  warn "Папка MQL5 не найдена в \"$SUPPORT\"."
  warn "Установите MetaTrader 5 для macOS, запустите его один раз, войдите в Bybit-аккаунт и повторите."
  exit 1
fi
COPIED=""
while IFS= read -r d; do
  [ -d "$d/Experts" ] || continue
  cp "$EA_SRC" "$d/Experts/ScalpScanExporter.mq5" && COPIED="$COPIED
$d/Experts/ScalpScanExporter.mq5" && ok "скопирован в $d/Experts"
done <<EOF
$MQL5_DIRS
EOF
COPIED="$(printf '%s' "$COPIED" | sed '/^$/d')"
if [ -z "$COPIED" ]; then
  warn "В найденных MQL5 нет папки Experts — откройте MT5 хотя бы раз и повторите."
  exit 1
fi

# ---------- 3. compile ----------
say "3/4 Компиляция советника"
APP="$(find "$APPS" -maxdepth 1 -iname 'MetaTrader*5*.app' 2>/dev/null | head -n 1)"
WINE=""
[ -n "$APP" ] && WINE="$(find "$APP/Contents" \( -name wine64 -o -name wine \) -type f -perm -100 2>/dev/null | head -n 1)"
COMPILED=0
if [ -n "$WINE" ]; then
  while IFS= read -r mq5; do
    prefix="${mq5%%/drive_c/*}"
    rel="${mq5#"$prefix"/drive_c/}"
    ed="$(find "$prefix/drive_c" -maxdepth 4 -iname 'metaeditor64.exe' 2>/dev/null | head -n 1)"
    [ -n "$ed" ] || continue
    winpath="C:\\$(printf '%s' "$rel" | tr '/' '\\')"
    WINEPREFIX="$prefix" WINEDEBUG=-all "$WINE" "$ed" "/compile:$winpath" /log >/dev/null 2>&1 &
    pid=$!
    for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill "$pid" 2>/dev/null
    if [ -f "${mq5%.mq5}.ex5" ]; then
      ok "скомпилирован: ${mq5%.mq5}.ex5"; COMPILED=1
    else
      log="${mq5%.mq5}.log"
      warn "компиляция не подтвердилась для $mq5"
      [ -f "$log" ] && iconv -f UTF-16LE -t UTF-8 "$log" 2>/dev/null | tail -n 15
    fi
  done <<EOF
$COPIED
EOF
else
  warn "Не найден Wine внутри приложения MetaTrader 5 в $APPS — автокомпиляция пропущена."
fi
if [ "$COMPILED" = 0 ]; then
  warn "Скомпилируйте вручную: в MT5 нажмите F4 → откройте Experts/ScalpScanExporter.mq5 → F7 (Compile)."
  warn "Если будут ошибки — пришлите их текст."
fi

# ---------- 4. next steps ----------
say "4/4 Запуск"
cat <<'EOF'
  Осталось одно действие в MT5:
    Навигатор → Советники → (правый клик → Обновить) → перетащите ScalpScanExporter на любой график.
    В углу графика появится «ScalpScanExporter: N symbols exported at …».
EOF

if [ "$RUN" = 1 ]; then
  say "Жду snapshot.json от советника (до 3 минут)…"
  for _ in $(seq 1 90); do
    snap="$(find "$SUPPORT" -maxdepth 12 -path '*scalpscan/snapshot.json' -mmin -2 2>/dev/null | head -n 1)"
    if [ -n "$snap" ]; then
      ok "найден $snap"
      cd "$REPO" && exec "$PY" -m scalpscan --snapshot "$snap" --save reports SCAN
    fi
    sleep 2
  done
  warn "snapshot.json не появился. Проверьте, что советник прикреплён к графику, и запустите:"
fi
printf '\n  cd "%s" && %s -m scalpscan\n\n' "$REPO" "$PY"
