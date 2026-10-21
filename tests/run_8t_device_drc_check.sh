#!/usr/bin/env bash
# Engine-specific baseline; the full public ASAP7 runset is a separate check.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-$repo_root/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then python_bin=python3; fi
args=(--engine "${DRC_ENGINE:-gdscheck}")
if [[ "${WRITE_BASELINE:-0}" == 1 ]]; then args+=(--write-baseline); fi
exec "$python_bin" "$repo_root/scripts/check_device_drc.py" "${args[@]}" "$@"
