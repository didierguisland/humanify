#!/bin/bash
# Compatible con bash 3.2; el despachador nunca interrumpe Claude.
_humanify_hooks=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/usr/bin/python3 "$_humanify_hooks/_humanify.py" dispatch "$@" 2>/dev/null
exit 0
