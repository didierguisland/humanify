#!/bin/bash
_humanify_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# El mismo archivo funciona por doble clic y desde otra terminal.
/usr/bin/osascript - "$_humanify_root/herramientas/humanify.sh" <<'APPLESCRIPT'
on run argv
    tell application "Terminal"
        activate
        do script "/bin/bash " & quoted form of (item 1 of argv)
    end tell
end run
APPLESCRIPT
