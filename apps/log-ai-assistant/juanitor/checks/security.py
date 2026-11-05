import ipaddress
import re
import subprocess

import requests

from juanitor import state
from juanitor.checks.system import finding

METRICS_URL = "http://127.0.0.1:6060/metrics"
COMMUNITY = {"CAPI", "lists"}
SSH_FAIL_WARN = 3
SSH_FAIL_URGENT = 20
ATTACK_WINDOW_HOURS = 2
ATTACK_WARN = 10
ATTACK_URGENT = 50
TAILSCALE = ipaddress.ip_network("100.64.0.0/10")
STRANGER_NOTE = ("Note: SSH is only meant to be reachable from the home "
                 "network or Tailscale. A successful login from an outside "
                 "address means someone else may know the password, or SSH "
                 "is exposed. Recommended: change the password and switch "
                 "to SSH keys only.")

FAILED = re.compile(r"Failed password|Invalid user")
ACCEPTED = re.compile(r"Accepted \S+ for (\S+) from (\S+)")
METRIC = re.compile(r'^(cs_active_decisions|cs_alerts)\{(.*)\}\s+(\S+)$')
LABEL = re.compile(r'(\w+)="([^"]*)"')


def ssh_lines(since="1 hour ago"):
    result = subprocess.run(
        ["journalctl", "-u", "ssh", "--since", since, "--no-pager", "-q"],
        capture_output=True, text=True, timeout=20,
    )
    return result.stdout.splitlines()


def trusted(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip in TAILSCALE


def check_ssh():
    try:
        lines = ssh_lines()
    except subprocess.TimeoutExpired:
        return [finding("security", "ssh_failures", "warning",
                        "Could not read the SSH log", "journalctl timed out")]

    failures = sum(1 for line in lines if FAILED.search(line))
    if failures >= SSH_FAIL_URGENT:
        level = "urgent"
    elif failures >= SSH_FAIL_WARN:
        level = "warning"
    else:
        level = "ok"
    results = [finding("security", "ssh_failures", level,
                       f"{failures} failed SSH logins in the last hour",
                       "SSH is not open to the internet, so failed logins "
                       "come from inside the home network or Tailscale",
                       value=failures)]

    strangers = sorted({f"{user} from {ip}"
                        for user, ip in ACCEPTED.findall("\n".join(lines))
                        if not trusted(ip)})
    if strangers:
        results.append(finding("security", "ssh_login", "urgent",
                               "SSH login from an unknown address",
                               ", ".join(strangers)))
    else:
        results.append(finding("security", "ssh_login", "ok",
                               "SSH logins only from home network or Tailscale",
                               "no logins from outside addresses in the last hour"))
    return results


def crowdsec_metrics():
    response = requests.get(METRICS_URL, timeout=10)
    response.raise_for_status()
    alerts = {}
    local_bans = 0
    for line in response.text.splitlines():
        match = METRIC.match(line)
        if not match:
            continue
        name, label_text, value = match.groups()
        labels = dict(LABEL.findall(label_text))
        if name == "cs_alerts" and labels.get("reason") != "test ban":
            alerts[labels.get("reason", "?")] = float(value)
        elif (name == "cs_active_decisions"
              and labels.get("origin") not in COMMUNITY):
            local_bans += float(value)
    return alerts, int(local_bans)


def check_crowdsec(db):
    try:
        alerts, local_bans = crowdsec_metrics()
    except requests.RequestException as error:
        return finding("security", "crowdsec", "warning",
                       "Cannot read CrowdSec metrics", str(error))

    total = int(sum(alerts.values()))
    recent = None
    change = state.change_over(db, "security:crowdsec",
                               days=ATTACK_WINDOW_HOURS / 24)
    if change is not None:
        old = int(change[0])
        recent = total - old if total >= old else total

    level = "ok"
    if recent is not None and recent >= ATTACK_URGENT:
        level = "urgent"
    elif recent is not None and recent >= ATTACK_WARN:
        level = "warning"

    if recent is None:
        title = "CrowdSec running (still collecting history)"
    else:
        title = (f"{recent} attacks detected by CrowdSec in the last "
                 f"{ATTACK_WINDOW_HOURS} hours")
    top = sorted(alerts.items(), key=lambda item: item[1], reverse=True)[:5]
    details = (f"{local_bans} IPs banned right now by this server's CrowdSec; "
               "top attack types since it started: "
               + ", ".join(f"{reason} ({int(count)})" for reason, count in top))
    return finding("security", "crowdsec", level, title, details, value=total)


def security_evidence(db, finding):
    if finding["key"].startswith("security:ssh"):
        lines = [line for line in ssh_lines()
                 if FAILED.search(line) or "Accepted" in line]
        if not lines:
            return "No failed or accepted SSH logins in the last hour."
        text = "Recent SSH log lines:\n" + "\n".join(lines[-12:])
        if finding["key"] == "security:ssh_login":
            text += "\n" + STRANGER_NOTE
        return text
    return f"CrowdSec summary: {finding['details']}"


def run_all(db):
    return check_ssh() + [check_crowdsec(db)]
