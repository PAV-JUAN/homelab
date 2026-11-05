import os
import subprocess

import requests

from juanitor import state
from juanitor.checks.containers import container_logs
from juanitor.checks.cpu import cpu_evidence
from juanitor.checks.security import security_evidence

NO_HISTORY_TEXT = ("History: unknown, there is no past data. "
                   "Do not say whether it grew, shrank or stayed the same.")


def disk_evidence(db, finding):
    mount = finding["key"].split(":", 1)[1]
    lines = []

    change = state.change_over(db, finding["key"], days=7)
    if change is None:
        lines.append(NO_HISTORY_TEXT)
    else:
        old, hours = change
        lines.append(f"History: {old:.0f}% full {hours:.0f} hours ago, "
                     f"{finding['value']:.0f}% now.")

    try:
        result = subprocess.run(
            ["du", "-x", "-k", "--max-depth=1", mount],
            capture_output=True, text=True, timeout=60,
        )
        rows = []
        for line in result.stdout.splitlines():
            size_kb, path = line.split("\t", 1)
            rows.append((int(size_kb), path))
        rows.sort(reverse=True)
        if len(rows) > 1:
            lines.append("Largest folders:")
            for size_kb, path in rows[1:7]:
                lines.append(f"  {path}: {size_kb / 1e6:.1f} GB")
        else:
            lines.append("Largest folders: none could be measured.")
        if "Permission denied" in result.stderr:
            lines.append("Note: some folders could not be read (permission "
                         "denied), so folder sizes are incomplete.")
    except subprocess.TimeoutExpired:
        lines.append("Folder sizes: scan took too long, skipped.")

    return "\n".join(lines)


BACKUP_CONTEXT = (
    "How backups work on this server: root's crontab runs "
    "/home/juan/backup-system/backup.sh every day at 07:30. "
    "It backs up with restic to /mnt/backup (an external SSD) and to "
    "Google Drive, then runs a restore test (verify.sh). Results are "
    "written to /var/log/homelab-backup.log. "
    "There is no systemd service for backups. "
    "A backup counts as missed after 26 hours."
)
BACKUP_MOUNT = "/mnt/backup"
BACKUP_LOG = "/var/log/homelab-backup.log"
BACKUP_KEYWORDS = ("FAILED", "ERROR", "Fatal", "error:", "STATUS",
                   "Backup started")


def mount_sentence(mounted):
    if mounted:
        return f"Mount check: the backup drive IS mounted at {BACKUP_MOUNT}."
    return (f"Mount check: the backup drive is NOT mounted at {BACKUP_MOUNT} "
            "right now (unplugged or failed to mount).")


def cron_ran():
    try:
        result = subprocess.run(
            ["journalctl", "-u", "cron", "--since", "26 hours ago",
             "--no-pager", "-q"],
            capture_output=True, text=True, timeout=20,
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    return "backup.sh" in result.stdout


def cron_sentence(ran):
    if ran is None:
        return "Cron check: the cron log could not be read."
    if ran:
        return "Cron check: cron DID start backup.sh in the last 26 hours."
    return "Cron check: cron did NOT start backup.sh in the last 26 hours."


def backup_evidence(db, finding):
    header = (BACKUP_CONTEXT + "\n"
              + mount_sentence(os.path.ismount(BACKUP_MOUNT)) + "\n"
              + cron_sentence(cron_ran()))
    try:
        with open(BACKUP_LOG, errors="replace") as log:
            lines = log.read().splitlines()
    except OSError as error:
        return header + f"\nBackup log could not be read: {error}"
    important = [line for line in lines[-200:]
                 if any(word in line for word in BACKUP_KEYWORDS)]
    if not important:
        return header + "\nNo error lines in the recent backup log."
    return header + "\nRecent backup log lines:\n" + "\n".join(important[-12:])


def container_note(name):
    return ("Note: paths in the logs are inside the container. "
            "To inspect files, use the server side of the mounts, "
            f"or: docker logs {name}")


def container_evidence(db, finding):
    name = finding["key"].split(":", 1)[1]
    lines = [f"Container: {name}. {finding['details']}.",
             container_note(name)]
    try:
        logs = [line[:200] for line in container_logs(name).splitlines()
                if line.strip()]
        if logs:
            lines.append("Last log lines:\n" + "\n".join(logs[-15:]))
        else:
            lines.append("Last log lines: (empty)")
    except requests.RequestException as error:
        lines.append(f"Logs could not be read: {error}")
    return "\n".join(lines)


EVIDENCE = {"disk": disk_evidence, "backup": backup_evidence,
            "container": container_evidence,
            "security": security_evidence,
            "cpu": cpu_evidence}


def collect(db, finding):
    collector = EVIDENCE.get(finding["check"])
    if collector is None:
        return "No extra evidence collected."
    return collector(db, finding)
