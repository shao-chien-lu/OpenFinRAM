#!/usr/bin/env bash
# Install the tested upstream revision as a standalone tool, local to this build.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_root="${1:-$repo_root/build/tools/gdscheck}"
# The pinned revision carries the full ASAP7 process verify_macro.py runs
# (--suite main) and the calibrated device deck.  GDSCHECK_SRC=<checkout>
# installs a local gdscheck instead.
if [ -n "${GDSCHECK_SRC:-}" ]; then
    cargo install --path "$GDSCHECK_SRC" --bin gdscheck --root "$install_root" --force
else
    cargo install --git https://github.com/jeffhsu3/gdscheck.git \
        --rev 592c8a79e236575039496c3b9897a6b374603d20 \
        --bin gdscheck --root "$install_root"
fi
echo "Installed $install_root/bin/gdscheck"
