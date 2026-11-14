#!/bin/bash
_humanify_tools=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec /usr/bin/python3 "$_humanify_tools/../hooks/_humanify.py" anota "$@"
