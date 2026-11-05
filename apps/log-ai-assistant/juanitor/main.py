from juanitor import ai, evidence, jellyfin, notify, state
from juanitor.checks import backup, containers, cpu, security, smart, system


def explain_text(db, f):
    proof = evidence.collect(db, f)
    answer = ai.explain(f, proof)
    if answer is None:
        return None
    return (f"{answer['summary']}\n"
            f"Cause: {answer['cause']}\n"
            f"Fix: {answer['fix']}\n"
            f"(AI confidence: {answer['confidence']})")


def handle_alert(db, f, streaming):
    if f["level"] == "urgent":
        notify.send(f["title"], f["details"], "urgent")
        text = explain_text(db, f)
        if text:
            notify.send(f"Why: {f['title']}", text, "warning")
        return
    if streaming:
        notify.send(f["title"],
                    f"{f['details']}\n\n(AI explanation skipped: "
                    "someone is watching Jellyfin)", "warning")
        return
    text = explain_text(db, f)
    message = f["details"] if text is None else f"{f['details']}\n\n{text}"
    notify.send(f["title"], message, "warning")


def main():
    db = state.connect()
    findings = (system.run_all() + backup.run_all() + smart.run_all()
                + containers.run_all(db) + security.run_all(db)
                + cpu.run_all(db))
    streaming = jellyfin.active_streams() > 0
    ai.THREADS = 2 if streaming else 4

    with db:
        for f in findings:
            state.save_reading(db, f)
            action = state.decide(db, f)
            if action == "alert":
                print(f"ALERT    [{f['level']}] {f['title']}")
                handle_alert(db, f, streaming)
            elif action == "resolved":
                print(f"RESOLVED {f['title']}")
                notify.send(f"Resolved: {f['title']}", f["details"], "resolved")

        state.cleanup(db)
        seen = {f["key"] for f in findings}
        if "container:proxy" not in seen:
            for key in state.forget_missing(db, "container:", seen):
                name = key.split(":", 1)[1]
                print(f"RESOLVED container {name} no longer exists")
                notify.send(f"Resolved: container {name} was removed",
                            "It no longer exists, so its alert is closed.",
                            "resolved")
    db.close()
    note = " (someone is watching Jellyfin)" if streaming else ""
    print(f"Checked {len(findings)} items{note}.")


if __name__ == "__main__":
    main()
