#!/bin/bash
_humanify_hooks=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/usr/bin/python3 "$_humanify_hooks/_humanify.py" guardia "$@" 2>/dev/null
exit 0
