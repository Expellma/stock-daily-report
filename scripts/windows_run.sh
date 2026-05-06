#!/usr/bin/env sh
# Run Stock Daily Report from Windows-friendly POSIX shells such as Git Bash,
# MSYS2, Cygwin, or WSL. The script creates/uses .venv and avoids relying on
# `source .venv/bin/activate`, which often fails on Windows.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'HELP'
Usage:
  sh scripts/windows_run.sh [stock-daily-report arguments]

Examples:
  sh scripts/windows_run.sh run
  sh scripts/windows_run.sh fisher NVDA --thesis "AI accelerator demand"
  sh scripts/windows_run.sh scheduler

If no arguments are supplied, the script defaults to:
  run --config config/settings.toml

Environment variables:
  PYTHON      Python executable to use when creating .venv.
  INSTALL_PROJECT=1  Also run `pip install --no-build-isolation -e .` before execution.
HELP
    exit 0
fi

find_python() {
    if [ -n "${PYTHON:-}" ]; then
        printf '%s\n' "$PYTHON"
        return 0
    fi

    for candidate in python3 python py; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    printf '%s\n' "ERROR: Python 3.10+ was not found. Install Python and retry, or set PYTHON=/path/to/python." >&2
    return 1
}

BASE_PYTHON=$(find_python)

if [ ! -d .venv ]; then
    echo "Creating virtual environment in .venv ..."
    "$BASE_PYTHON" -m venv .venv
fi

if [ -x .venv/Scripts/python.exe ]; then
    VENV_PYTHON=.venv/Scripts/python.exe
elif [ -x .venv/bin/python ]; then
    VENV_PYTHON=.venv/bin/python
else
    printf '%s\n' "ERROR: .venv exists but no Python executable was found under .venv/Scripts or .venv/bin." >&2
    exit 1
fi

"$VENV_PYTHON" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required; found {sys.version.split()[0]}")
PY

if [ "${INSTALL_PROJECT:-0}" = "1" ]; then
    echo "Installing project into the virtual environment ..."
    "$VENV_PYTHON" -m pip install --no-build-isolation -e .
fi

if [ "$#" -eq 0 ]; then
    set -- run --config config/settings.toml
fi

echo "Running: stock-daily-report $*"
exec "$VENV_PYTHON" -m stock_daily_report.cli "$@"
