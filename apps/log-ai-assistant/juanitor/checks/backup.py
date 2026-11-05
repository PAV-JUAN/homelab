import re
import time
from pathlib import Path

from juanitor.checks.system import finding

LOG = Path("/var/log/homelab-backup.log")
MAX_AGE_HOURS = 26
STATUS_LINE = re.compile(r"^(BACKUP_STATUS|RESTORE_TEST)=(OK|FAILED) (\d+)")


def latest_statuses():
    latest = {}
    for line in LOG.read_text(errors="replace").splitlines():
        match = STATUS_LINE.match(line)
        if match:
            name, result, ts = match.groups()
            latest[name] = (result, int(ts))
    return latest


def check_backup():
    if not LOG.exists():
        return [finding("backup", "run", "urgent",
                        "Backup log not found", str(LOG))]
    latest = latest_statuses()
    results = []

    backup = latest.get("BACKUP_STATUS")
    if backup is None:
        results.append(finding("backup", "run", "urgent",
                               "No backup result found",
                               "the log has no timestamped BACKUP_STATUS line"))
    else:
        result, ts = backup
        hours = (time.time() - ts) / 3600
        if result == "FAILED":
            level, title = "urgent", "Last backup FAILED"
        elif hours > MAX_AGE_HOURS:
            level, title = "urgent", f"No successful backup for {hours:.0f} hours"
        else:
            level, title = "ok", "Backup succeeded"
        results.append(finding("backup", "run", level, title,
                               f"last result {result}, {hours:.0f} hours ago",
                               value=round(hours, 1)))

    test = latest.get("RESTORE_TEST")
    if test is not None:
        result, ts = test
        level = "warning" if result == "FAILED" else "ok"
        results.append(finding("backup", "restore_test", level,
                               f"Restore test {result}",
                               f"{(time.time() - ts) / 3600:.0f} hours ago"))
    return results


def run_all():
    return check_backup()
