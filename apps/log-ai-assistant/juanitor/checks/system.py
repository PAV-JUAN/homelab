import psutil

DISK_WARN = 80
DISK_URGENT = 90
TEMP_WARN = 75
TEMP_URGENT = 85
IGNORED_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay"}


def finding(check, target, level, title, details, value=None):
    return {
        "key": f"{check}:{target}",
        "check": check,
        "level": level,
        "title": title,
        "details": details,
        "value": value,
    }


def level_for(value, warn, urgent):
    if value >= urgent:
        return "urgent"
    if value >= warn:
        return "warning"
    return "ok"


def check_disks():
    results = []
    seen = set()
    for part in psutil.disk_partitions():
        if part.fstype in IGNORED_FS or part.device in seen:
            continue
        seen.add(part.device)
        usage = psutil.disk_usage(part.mountpoint)
        results.append(finding(
            "disk",
            part.mountpoint,
            level_for(usage.percent, DISK_WARN, DISK_URGENT),
            f"{part.mountpoint} is {usage.percent:.0f}% full",
            f"{usage.free / 1e9:.1f} GB free of {usage.total / 1e9:.1f} GB",
            value=usage.percent,
        ))
    return results


def check_temperature():
    cores = psutil.sensors_temperatures().get("coretemp", [])
    if not cores:
        return [finding("temperature", "cpu", "warning",
                        "No CPU temperature sensor found",
                        "the coretemp sensor is not available")]
    hottest = max(sensor.current for sensor in cores)
    return [finding(
        "temperature",
        "cpu",
        level_for(hottest, TEMP_WARN, TEMP_URGENT),
        f"CPU at {hottest:.0f}°C",
        f"hottest of {len(cores)} sensors",
        value=hottest,
    )]


def check_memory():
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    available_pct = mem.available / mem.total * 100
    level = "warning" if available_pct < 10 or swap.percent > 50 else "ok"
    return [finding(
        "memory",
        "ram",
        level,
        f"{available_pct:.0f}% RAM available",
        f"swap {swap.percent:.0f}% used",
        value=round(available_pct, 1),
    )]


def run_all():
    return check_disks() + check_temperature() + check_memory()


if __name__ == "__main__":
    for item in run_all():
        print(f"[{item['level'].upper():7}] {item['check']:12} "
              f"{item['title']} ({item['details']})")
