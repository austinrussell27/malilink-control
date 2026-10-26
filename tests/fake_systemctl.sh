#!/bin/sh
# Test stand-in for systemctl: logs calls, reports units as not loaded/inactive.
[ -n "$FAKE_SYSTEMCTL_LOG" ] && echo "$@" >> "$FAKE_SYSTEMCTL_LOG"
if [ "$1" = "show" ]; then echo "ActiveState=inactive"; echo "SubState=dead"; echo "LoadState=not-found"; fi
exit 0
