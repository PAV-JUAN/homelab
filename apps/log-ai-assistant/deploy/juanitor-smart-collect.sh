#!/bin/bash
# Juanitor SMART collector: runs as root (hourly), saves each drive's
# SMART data as JSON so Juanitor can read it without root access.
set -u
OUT=/var/lib/juanitor-smart
rm -f "$OUT"/*.json
smartctl --scan | while read -r dev _ type _; do
    file="$OUT/$(basename "$dev").json"
    smartctl --json -i -H -A -d "$type" "$dev" > "$file"
    if ! grep -q '"smart_status"' "$file"; then
        smartctl --json -i -H -A -d sat "$dev" > "$file"
    fi
done
chmod 644 "$OUT"/*.json 2>/dev/null
exit 0
