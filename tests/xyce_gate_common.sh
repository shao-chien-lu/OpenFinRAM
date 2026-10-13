#!/bin/bash

# Locate Xyce and verify that its runtime can initialize.  Some restricted
# environments allow the executable to be inspected but prevent the MPI
# runtime from opening its local socket.  CTest treats exit 77 as a skip, so
# do that here instead of letting every characterization measure look like an
# electrical sanity failure.
require_xyce() {
    local bundled_xyce="/home/jeff/iv4/local/xyce-14.4/bin/Xyce"

    if [ -n "${XYCE:-}" ] && [ -x "$XYCE" ]; then
        :
    elif [ -x "$bundled_xyce" ]; then
        XYCE="$bundled_xyce"
    else
        XYCE="$(command -v Xyce || true)"
    fi

    if [ -z "${XYCE:-}" ] || [ ! -x "$XYCE" ]; then
        echo "SKIP: Xyce not available"
        exit 77
    fi

    if ! "$XYCE" -v >/dev/null 2>&1; then
        echo "SKIP: Xyce is installed but cannot initialize in this environment"
        exit 77
    fi
}

# Assert a Xyce .measure result falls inside an inclusive window, and echo it
# so a passing run reads as a table rather than as silence.
#
#   check_measure <measure-file> <NAME> <min> <max> [units]
#
# <measure-file> is whatever Xyce wrote: .mt0 for transient measures, .ms0 for
# DC ones.  It also accepts any "NAME = value" file, which is what the
# post-processors emit, so a derived quantity is bounded the same way a raw
# measure is.  Names are matched exactly and Xyce upcases them, so pass the
# uppercase form.  [units] defaults to V and is only a display label.
# The float comparison runs in awk because bash cannot do it.
#
# run_8t_ioprech_spice_check.sh predates this and carries its own identical
# copy, which shadows this one when that script runs.  That copy can go
# whenever someone is editing that file anyway; it is left alone here so this
# addition cannot perturb a passing gate.
check_measure() {
    local measures="$1"
    local name="$2"
    local minimum="$3"
    local maximum="$4"
    local units="${5:-V}"
    local value
    value="$(awk -v key="$name" '$1 == key { print $3 }' "$measures")"
    if [ -z "$value" ]; then
        echo "FAIL: $name was not measured in $measures" >&2
        exit 1
    fi
    if ! awk -v value="$value" -v lo="$minimum" -v hi="$maximum" \
        'BEGIN { exit !(value >= lo && value <= hi) }'; then
        echo "FAIL: $name=$value, expected [$minimum, $maximum]" >&2
        exit 1
    fi
    printf '  %-18s %s %s\n' "$name" "$value" "$units"
}
