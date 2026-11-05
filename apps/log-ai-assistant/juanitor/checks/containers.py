import os

import requests
from dotenv import load_dotenv

from juanitor import state
from juanitor.checks.system import finding

load_dotenv()

PROXY = os.environ.get("DOCKER_PROXY_URL", "http://127.0.0.1:2375")
RESTART_WINDOW_HOURS = 2
RESTART_LIMIT = 3
KEEP_RUNNING = {"always", "unless-stopped"}


def get(path):
    response = requests.get(f"{PROXY}{path}", timeout=10)
    response.raise_for_status()
    return response.json()


def container_logs(name, lines=20):
    response = requests.get(
        f"{PROXY}/containers/{name}/logs",
        params={"stdout": 1, "stderr": 1, "tail": lines},
        timeout=10,
    )
    response.raise_for_status()
    raw = response.content
    parts = []
    while len(raw) >= 8 and raw[0] in (0, 1, 2) and raw[1:4] == b"\x00\x00\x00":
        size = int.from_bytes(raw[4:8], "big")
        parts.append(raw[8:8 + size])
        raw = raw[8 + size:]
    data = b"".join(parts) if parts else raw
    return data.decode(errors="replace")


def check_container(db, info):
    name = info["Name"].lstrip("/")
    st = info["State"]
    restarts = info.get("RestartCount", 0)
    policy = info["HostConfig"]["RestartPolicy"]["Name"] or "none"
    health = st.get("Health", {}).get("Status")
    key = f"container:{name}"

    problems = []
    level = "ok"
    if st.get("OOMKilled"):
        problems.append("was killed for using too much memory (OOM)")
        level = "urgent"
    if st["Status"] == "restarting":
        problems.append("is stuck restarting")
        level = "urgent"
    elif (st["Status"] == "exited" and st.get("ExitCode", 0) != 0
          and policy in KEEP_RUNNING):
        problems.append(f"crashed and stayed down (exit code {st['ExitCode']})")
        level = "urgent"

    change = state.change_over(db, key, days=RESTART_WINDOW_HOURS / 24)
    if change is not None:
        old, _ = change
        recent = restarts - int(old)
        if recent >= RESTART_LIMIT:
            problems.append(f"restarted {recent} times in the last "
                            f"{RESTART_WINDOW_HOURS} hours")
            level = "urgent"

    if health == "unhealthy":
        problems.append("its health check says unhealthy")
        if level == "ok":
            level = "warning"

    title = f"Container {name}: " + ("; ".join(problems) if problems else st["Status"])
    mounts = ", ".join(f"{m['Source']} -> {m['Destination']}"
                       for m in info.get("Mounts", [])) or "none"
    details = (f"status {st['Status']}, {restarts} restarts total, "
               f"restart policy {policy}, mounts (server -> container): "
               f"{mounts}")
    return finding("container", name, level, title, details, value=restarts)


def run_all(db):
    try:
        containers = get("/containers/json?all=1")
        return [check_container(db, get(f"/containers/{c['Id']}/json"))
                for c in containers]
    except requests.RequestException as error:
        return [finding("container", "proxy", "warning",
                        "Cannot read Docker containers",
                        f"socket proxy not reachable: {error}")]
