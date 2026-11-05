import json
import time
from pathlib import Path

from juanitor.checks.system import finding

SMART_DIR = Path("/var/lib/juanitor-smart")
MAX_AGE_HOURS = 3
TEMP_WARN = 60
TEMP_URGENT = 70
DAMAGE_URGENT = 10
DAMAGE = {5: "reallocated sectors", 187: "uncorrectable errors",
          196: "reallocation events", 197: "pending sectors",
          198: "offline uncorrectable sectors"}
CRC_ERRORS = 199


def read_drives():
    drives = []
    for path in sorted(SMART_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if "smart_status" in data:
            drives.append((path, data))
    return drives


def check_drive(path, data):
    model = data.get("model_name", path.stem)
    table = data.get("ata_smart_attributes", {}).get("table", [])
    raw = {attr["id"]: attr["raw"]["value"] for attr in table}

    problems = []
    level = "ok"
    if not data["smart_status"].get("passed", True):
        problems.append("SMART health FAILED")
        level = "urgent"

    damage = {name: raw[i] for i, name in DAMAGE.items() if raw.get(i, 0) > 0}
    if damage:
        problems.append(", ".join(f"{count} {name}" for name, count in damage.items()))
        total = sum(damage.values())
        level = "urgent" if level == "urgent" or total >= DAMAGE_URGENT else "warning"

    if raw.get(CRC_ERRORS, 0) > 0:
        problems.append(f"{raw[CRC_ERRORS]} cable/connection (CRC) errors")
        if level == "ok":
            level = "warning"

    temp = data.get("temperature", {}).get("current")
    if temp is not None and temp >= TEMP_WARN:
        problems.append(f"temperature {temp}°C")
        level = "urgent" if temp >= TEMP_URGENT else max(level, "warning",
                                                          key=["ok", "warning", "urgent"].index)

    hours = data.get("power_on_time", {}).get("hours", "?")
    details = f"{model}, {hours} power-on hours, {temp}°C"
    title = f"Drive {model}: " + ("; ".join(problems) if problems else "healthy")
    return finding("smart", model.replace(" ", "_"), level, title, details,
                   value=temp)


def check_freshness():
    files = list(SMART_DIR.glob("*.json"))
    if not files:
        return finding("smart", "collector", "warning",
                       "No SMART data found",
                       f"{SMART_DIR} is empty; is juanitor-smart.timer running?")
    hours = (time.time() - max(f.stat().st_mtime for f in files)) / 3600
    level = "warning" if hours > MAX_AGE_HOURS else "ok"
    title = ("SMART data is stale" if level == "warning"
             else "SMART collector running")
    return finding("smart", "collector", level, title,
                   f"last collected {hours:.1f} hours ago")


def run_all():
    return [check_freshness()] + [check_drive(p, d) for p, d in read_drives()]
