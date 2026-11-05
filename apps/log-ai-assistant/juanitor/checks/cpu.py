import os
import time

import psutil

from juanitor import state
from juanitor.checks.system import finding

CORES = os.cpu_count() or 1
LOAD_WARN = 0.9
LOAD_URGENT = 1.5


def check_cpu(db):
    load15 = os.getloadavg()[2]
    ratio = load15 / CORES
    before = state.previous(db, "cpu:load")
    sustained = before is not None and before >= LOAD_WARN

    if ratio >= LOAD_URGENT and sustained:
        level = "urgent"
    elif ratio >= LOAD_WARN and sustained:
        level = "warning"
    else:
        level = "ok"

    if level == "ok":
        title = f"CPU load {ratio:.0%} (15-minute average)"
    else:
        title = f"CPU overloaded at {ratio:.0%} for 25+ minutes"
    details = f"15-minute load average {load15:.1f} on {CORES} cores"
    return [finding("cpu", "load", level, title, details, value=round(ratio, 2))]


def top_processes(count=5, seconds=2):
    procs = list(psutil.process_iter(["name"]))
    for proc in procs:
        try:
            proc.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(seconds)
    usage = []
    for proc in procs:
        try:
            usage.append((proc.cpu_percent(None), proc.info["name"]))
        except psutil.Error:
            pass
    usage.sort(reverse=True)
    return [f"{name}: {percent:.0f}% CPU" for percent, name in usage[:count]]


TRANSCODE_NOTE = ("Note: ffmpeg is usually Jellyfin converting a video for "
                  "someone watching right now. Stopping it would stop their "
                  "movie, so do not kill it; the load normally ends when the "
                  "movie ends.")


def cpu_evidence(db, finding):
    top = top_processes()
    text = "Top processes right now:\n" + "\n".join(top)
    if any(line.startswith(("ffmpeg", "jellyfin")) for line in top):
        text += "\n" + TRANSCODE_NOTE
    return text


def run_all(db):
    return check_cpu(db)
