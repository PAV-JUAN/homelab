#!/bin/bash
# Nightly homelab backup:
#   - USB drive: everything important, including music
#   - Google Drive: everything important except music (encrypted)
set -euo pipefail

trap 'echo "BACKUP_STATUS=FAILED $(date +%s) line $LINENO"' ERR
LOCAL_REPO=/mnt/backup/restic
CLOUD_REPO=rclone:gdrive:homelab-backup
export RESTIC_PASSWORD_FILE=/root/restic-password
STAGING=/var/backups/homelab-staging
H=/home/juan
VOLUMES=/var/lib/docker/volumes

# What to back up
PATHS=(
    "$STAGING"
    "$H/vaultwarden"
    "$H/flight-tracker"
    "$H/homepage"
    "$H/mail-notifier"
    "$H/mail2jira"
    "$H/homelab"
    "$H/pihole"
    "$H/torrent"
    "$H/media-server"
    "$H/yt2mp3"
    "$VOLUMES/flight-tracker_grafana-data"
    "$VOLUMES/portainer_data"
    "$VOLUMES/yt2mp3_yt2mp3-data"
)

# What to skip everywhere
EXCLUDES=(
    --exclude "$H/flight-tracker/pgdata"
    --exclude "$H/media-server/media"
    --exclude "$H/media-server/jellyfin/cache"
    --exclude "$H/torrent/incomplete"
    --exclude "venv"
    --exclude "__pycache__"
    --exclude "*.log"
)

# How many old backups to keep
KEEP=(--keep-daily 7 --keep-weekly 4 --keep-monthly 6)

echo "=== Backup finished OK: $(date) ==="
echo "BACKUP_STATUS=OK $(date +%s)"


# Make sure the USB is actually mounted (not just an empty folder)
if ! mountpoint -q /mnt/backup; then
    echo "ERROR: backup drive is not mounted"
    exit 1
fi

mkdir -p "$STAGING"

echo "juanitor-canary-v1" > "$STAGING/canary.txt"

# 1. Safe copies of databases that are in use
echo "Exporting flight tracker database..."
docker exec flight-db pg_dump -U flights -d flights > "$STAGING/flights.sql"

echo "Copying Vaultwarden database safely..."
sqlite3 "$H/vaultwarden/data/db.sqlite3" ".backup '$STAGING/vaultwarden.sqlite3'"

# 2. USB drive: everything
echo "--- USB drive ---"
restic -r "$LOCAL_REPO" backup "${PATHS[@]}" "${EXCLUDES[@]}"
restic -r "$LOCAL_REPO" forget "${KEEP[@]}" --prune
restic -r "$LOCAL_REPO" check

# 3. Google Drive: everything except music
echo "--- Google Drive ---"
restic -r "$CLOUD_REPO" backup "${PATHS[@]}" "${EXCLUDES[@]}" --exclude "$H/yt2mp3/music"
restic -r "$CLOUD_REPO" forget "${KEEP[@]}" --prune
restic -r "$CLOUD_REPO" check

   echo "=== Backup finished OK: $(date) ==="
   echo "BACKUP_STATUS=OK $(date +%s)"

   # 4. Prove the backups can actually be restored (never blocks the backup)
   if /home/juan/backup-system/verify.sh; then
       echo "RESTORE_TEST=OK $(date +%s)"
   else
       echo "RESTORE_TEST=FAILED $(date +%s)"
   fi
