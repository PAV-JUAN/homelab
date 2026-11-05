import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "juanitor.db"
REMIND_AFTER_HOURS = 24
LEVEL_RANK = {"ok": 0, "warning": 1, "urgent": 2}


def connect():
    DB_PATH.parent.mkdir(exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("""CREATE TABLE IF NOT EXISTS readings (
        ts INTEGER, key TEXT, value REAL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS alerts (
        key TEXT PRIMARY KEY, level TEXT, last_sent INTEGER)""")
    return db


def save_reading(db, finding):
    if finding["value"] is not None:
        db.execute("INSERT INTO readings VALUES (?, ?, ?)",
                   (int(time.time()), finding["key"], finding["value"]))


def decide(db, finding):
    now = int(time.time())
    row = db.execute("SELECT level, last_sent FROM alerts WHERE key = ?",
                     (finding["key"],)).fetchone()
    level = finding["level"]

    if level == "ok":
        if row:
            db.execute("DELETE FROM alerts WHERE key = ?", (finding["key"],))
            return "resolved"
        return None

    escalated = row is None or LEVEL_RANK[level] > LEVEL_RANK[row[0]]
    stale = row is not None and now - row[1] > REMIND_AFTER_HOURS * 3600
    if escalated or stale:
        db.execute("INSERT OR REPLACE INTO alerts VALUES (?, ?, ?)",
                   (finding["key"], level, now))
        return "alert"
    return None

def change_over(db, key, days):
    since = int(time.time()) - days * 86400
    row = db.execute(
        "SELECT ts, value FROM readings WHERE key = ? AND ts >= ? "
        "ORDER BY ts LIMIT 1",
        (key, since)).fetchone()
    if row is None:
        return None
    hours = (time.time() - row[0]) / 3600
    if hours < 1:
        return None
    return row[1], hours


def forget_missing(db, prefix, seen_keys):
    rows = db.execute("SELECT key FROM alerts WHERE key LIKE ?",
                      (prefix + "%",)).fetchall()
    gone = [key for (key,) in rows if key not in seen_keys]
    for key in gone:
        db.execute("DELETE FROM alerts WHERE key = ?", (key,))
    return gone


def previous(db, key):
    row = db.execute("SELECT value FROM readings WHERE key = ? "
                     "ORDER BY ts DESC LIMIT 1", (key,)).fetchone()
    return row[0] if row else None


def cleanup(db, days=30):
    cutoff = int(time.time()) - days * 86400
    db.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
