#!/bin/bash
# Restore test: proves the latest backups can actually be restored and used
set -euo pipefail

LOCAL_REPO=/mnt/backup/restic
CLOUD_REPO=rclone:gdrive:homelab-backup
export RESTIC_PASSWORD_FILE=/root/restic-password
STAGING=/var/backups/homelab-staging
H=/home/juan
TEST_DIR=$(mktemp -d /tmp/restore-test.XXXXXX)
DB_CONTAINER=restore-test-db

fail() {
    echo "RESTORE TEST FAILED: $1"
    exit 1
}

# Always clean up the temporary files and test database, even if a check fails
cleanup() {
    rm -rf "$TEST_DIR"
    docker rm -f "$DB_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== Restore test started: $(date) ==="

# 1. Read a random 5% of the stored data to catch silent damage
echo "Reading a sample of the backup data..."
restic -r "$LOCAL_REPO" check --read-data-subset=5%

# 2. Restore the staging folder from the USB backup
echo "Restoring test files from the USB drive..."
restic -r "$LOCAL_REPO" restore latest --target "$TEST_DIR/usb" \
    --include "$STAGING"

# 3. The canary file must come back exactly as written
[ "$(cat "$TEST_DIR/usb$STAGING/canary.txt")" = "juanitor-canary-v1" ] \
    || fail "restored canary file does not match"
echo "OK: canary file restored identically"

# 4. Vaultwarden: healthy database, with passwords in it
VW="$TEST_DIR/usb$STAGING/vaultwarden.sqlite3"
[ "$(sqlite3 "$VW" 'PRAGMA integrity_check;')" = "ok" ] \
    || fail "restored Vaultwarden database is damaged"
ITEMS=$(sqlite3 "$VW" 'SELECT COUNT(*) FROM ciphers;')
[ "$ITEMS" -gt 0 ] || fail "restored Vaultwarden database has no passwords"
echo "OK: Vaultwarden database is healthy ($ITEMS items)"

# 5. Flight database: load the export into a temporary PostgreSQL
echo "Loading the flight database into a temporary PostgreSQL..."
docker run -d --name "$DB_CONTAINER" -e POSTGRES_PASSWORD=test postgres:17 >/dev/null
for i in $(seq 1 30); do
    docker exec "$DB_CONTAINER" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break
    sleep 2
done
docker exec -i "$DB_CONTAINER" psql -q -U postgres \
    < "$TEST_DIR/usb$STAGING/flights.sql" >/dev/null 2>&1 || true
ROWS=$(docker exec "$DB_CONTAINER" psql -U postgres -tAc "SELECT COUNT(*) FROM fare_calendar;" || echo 0)
[ "$ROWS" -gt 0 ] || fail "restored flight database has no price data"
echo "OK: flight database restored ($ROWS price rows)"

# 6. Google Drive: the cloud copy must restore too
echo "Restoring Vaultwarden from Google Drive..."
restic -r "$CLOUD_REPO" restore latest --target "$TEST_DIR/cloud" \
    --include "$STAGING/vaultwarden.sqlite3"
[ "$(sqlite3 "$TEST_DIR/cloud$STAGING/vaultwarden.sqlite3" 'PRAGMA integrity_check;')" = "ok" ] \
    || fail "Vaultwarden restored from Google Drive is damaged"
echo "OK: Google Drive copy restores correctly"

echo "=== Restore test PASSED: $(date) ==="
