#!/bin/bash
# Convenience wrapper for repeat runs: activates the virtual environment and
# runs the full pipeline, no matter where in Terminal you are when you call
# this script. Use this from now on instead of the cd + source + vobb-read
# sequence -- just double-click-drag this file into Terminal (or type its
# path) and press Enter.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate
vobb-read run --headed "$@"
