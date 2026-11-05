import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
TIMEOUT_SECONDS = 180
THREADS = 4

SYSTEM_PROMPT = """You are Juanitor, a sysadmin assistant for a small home Ubuntu server.
You get one problem and the evidence collected about it. Answer in JSON.

Fields:
- summary: one sentence explaining what is going on, in plain words. Do not just repeat the problem title.
- cause: the most likely reason, based ONLY on the evidence. If the evidence is not enough, write "Not enough evidence to tell."
- fix: one concrete next step for a human: a command to inspect, or a safe action.
- confidence: "high" if the evidence clearly shows the cause, "medium" if it suggests it, "low" if it is a guess.

Rules:
- Never invent file names, numbers or services.
- If the history is unknown, do not say anything grew, shrank or stayed the same.
- If folder sizes are incomplete or missing, say so instead of guessing what is inside.
- Keep each field under 25 words.

Example 1:
Problem: /srv is 93% full (2 GB free of 30 GB). Level: urgent.
Evidence: History: 70% full 48 hours ago, 93% now. Largest folders: /srv/downloads 24 GB, /srv/app 3 GB.
Answer: {"summary": "/srv filled up fast in two days and will be full soon.", "cause": "/srv/downloads grew to 24 GB and takes most of the disk.", "fix": "Check the newest files with: ls -lt /srv/downloads | head", "confidence": "high"}

Example 2:
Problem: /data is 86% full (4 GB free of 30 GB). Level: warning.
Evidence: History: unknown, there is no past data. Largest folders: /data/photos 22 GB, /data/docs 1 GB.
Answer: {"summary": "/data is mostly used by the photos folder.", "cause": "/data/photos takes 22 GB, most of the disk. Past usage is unknown.", "fix": "See the biggest items with: du -sh /data/photos/* | sort -h | tail", "confidence": "medium"}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "cause": {"type": "string"},
        "fix": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["summary", "cause", "fix", "confidence"],
}

DANGEROUS = ("rm -rf", "mkfs", "dd if=", "> /dev/", "chmod -R 777")
SAFE_COMMANDS = ("ls", "du", "df", "cat", "grep", "tail", "head", "free",
                 "uptime", "top", "sensors", "findmnt", "lsblk",
                 "journalctl", "smartctl -a", "smartctl -H", "smartctl -A", "docker ps", "docker logs",
                 "systemctl status", "crontab -l", "crontab -u root -l")

GROWTH_WORDS = ("grew", "grown", "growing", "increas", "stable",
                "no change", "filled up")
NO_HISTORY = "History: unknown"
SAFE_CAUSE = "Past usage is unknown, so it is not clear if this disk is filling up."

NOT_MOUNTED = "backup drive is NOT mounted"
MOUNT_WORDS = ("mount", "plug", "connect")


def is_safe(command):
    command = command.strip()
    if command.startswith("sudo "):
        command = command[len("sudo "):]
    return any(command == safe or command.startswith(safe + " ")
               for safe in SAFE_COMMANDS)


def ask(messages):
    try:
        response = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "format": SCHEMA,
                "stream": False,
                "options": {"temperature": 0.2, "num_thread": THREADS,
                            "num_predict": 350},
            },
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
    except (requests.RequestException, ValueError, KeyError) as error:
        print(f"AI unavailable: {error}")
        return None

    try:
        raw = json.loads(content)
        return {field: str(raw[field]) for field in SCHEMA["required"]}
    except (ValueError, KeyError) as error:
        print(f"AI answer unusable ({error}): {content[:300]}")
        return None


def find_issues(answer, evidence):
    issues = []
    if NO_HISTORY in evidence:
        text = f"{answer['summary']} {answer['cause']}".lower()
        found = [word for word in GROWTH_WORDS if word in text]
        if found:
            issues.append(("history",
                           f"you described a change in usage ({', '.join(found)}) "
                           "but the history is unknown. Describe only what uses "
                           "the space now"))
    if NOT_MOUNTED in evidence:
        text = " ".join([answer["summary"], answer["cause"], answer["fix"]]).lower()
        if not any(word in text for word in MOUNT_WORDS):
            issues.append(("mount",
                           "the evidence says the backup drive is NOT mounted, "
                           "but your answer does not mention it"))
    return issues


def explain(finding, evidence):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Problem: {finding['title']} ({finding['details']}). "
            f"Level: {finding['level']}.\n"
            f"Evidence: {evidence}")},
    ]
    answer = ask(messages)
    if answer is None:
        return None

    issues = find_issues(answer, evidence)
    if issues:
        feedback = "; ".join(message for _, message in issues)
        print(f"    (retrying: {feedback})")
        messages += [
            {"role": "assistant", "content": json.dumps(answer)},
            {"role": "user", "content": (
                f"Fix your answer: {feedback}. "
                "Answer again in the same JSON format.")},
        ]
        retry = ask(messages)
        if retry is not None:
            answer = retry

    for kind, _ in find_issues(answer, evidence):
        print(f"    (fallback: {kind})")
        if kind == "history":
            answer["cause"] = SAFE_CAUSE
            answer["summary"] = f"{finding['title']}."
        elif kind == "mount":
            answer["cause"] = ("The backup drive is not mounted at /mnt/backup, "
                               "so restic cannot reach its repository.")
            answer["fix"] = "Check if the drive is connected with: findmnt /mnt/backup"

    if NO_HISTORY in evidence and answer["confidence"] == "high":
        answer["confidence"] = "medium"

    if NOT_MOUNTED in evidence and answer["confidence"] == "low":
        answer["confidence"] = "medium"

    if any(bad in answer["fix"] for bad in DANGEROUS):
        answer["fix"] = "(hidden: the suggestion contained a destructive command)"
    elif ":" in answer["fix"]:
        advice, command = answer["fix"].split(":", 1)
        if not is_safe(command):
            print(f"    (unsafe command hidden: {command.strip()})")
            advice = advice.strip().removesuffix(" with").removesuffix(" by running")
            answer["fix"] = advice + " (suggested command hidden: not on the safe list)"
    return answer
