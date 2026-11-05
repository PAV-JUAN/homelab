import argparse
import json
import time
from pathlib import Path

from juanitor import ai
from juanitor.checks.cpu import TRANSCODE_NOTE
from juanitor.checks.security import STRANGER_NOTE
from juanitor.evidence import (BACKUP_CONTEXT, container_note, cron_sentence,
                               mount_sentence)

HERE = Path(__file__).parent
SCENARIOS = HERE / "scenarios.json"
REPORT = HERE / "last_run.md"


def grade(scenario, answer):
    if answer is None:
        return ["no usable answer from the AI"]
    problems = []
    text = " ".join([answer["summary"], answer["cause"], answer["fix"]]).lower()
    for group in scenario.get("must_mention", []):
        if not any(word.lower() in text for word in group):
            problems.append(f"should mention one of {group}")
    for word in scenario.get("must_not_mention", []):
        if word.lower() in text:
            problems.append(f"should not mention '{word}'")
    allowed = scenario.get("confidence_in")
    if allowed and answer["confidence"] not in allowed:
        problems.append(f"confidence '{answer['confidence']}' not in {allowed}")
    return problems


def build_evidence(scenario):
    evidence = scenario["evidence"]
    if scenario.get("backup_context"):
        mounted = scenario.get("mounted", True)
        ran = scenario.get("cron_ran", True)
        evidence = (BACKUP_CONTEXT + "\n" + mount_sentence(mounted) + "\n"
                    + cron_sentence(ran) + "\n" + evidence)
    if scenario.get("container"):
        name = scenario["container"]
        header = f"Container: {name}. {scenario['finding']['details']}."
        evidence = header + "\n" + container_note(name) + "\n" + evidence
    if scenario.get("cpu_note"):
        evidence += "\n" + TRANSCODE_NOTE
    if scenario.get("ssh_note"):
        evidence += "\n" + STRANGER_NOTE
    return evidence


def main():
    parser = argparse.ArgumentParser(description="Score the AI on test scenarios")
    parser.add_argument("only", nargs="?", help="run only this scenario id")
    parser.add_argument("--repeat", type=int, default=1, help="runs per scenario")
    args = parser.parse_args()

    scenarios = [s for s in json.loads(SCENARIOS.read_text())
                 if not args.only or s["id"] == args.only]
    rows = []
    total_passed = total_runs = 0
    for s in scenarios:
        passed = 0
        seconds = []
        last_fail = None
        for _ in range(args.repeat):
            start = time.time()
            answer = ai.explain(s["finding"], build_evidence(s))
            seconds.append(time.time() - start)
            problems = grade(s, answer)
            if problems:
                last_fail = (answer, problems)
            else:
                passed += 1
        total_passed += passed
        total_runs += args.repeat
        avg = sum(seconds) / len(seconds)
        print(f"[{passed}/{args.repeat}] {s['id']} (avg {avg:.0f}s)")
        if last_fail:
            answer, problems = last_fail
            if answer:
                print(f"    last failing answer: {answer['summary']} | "
                      f"{answer['cause']} | {answer['fix']} [{answer['confidence']}]")
            for problem in problems:
                print(f"    -> {problem}")
        rows.append(f"| {s['id']} | {passed}/{args.repeat} | {avg:.0f}s |")

    rate = 100 * total_passed / total_runs if total_runs else 0
    summary = (f"Pass rate: {total_passed}/{total_runs} ({rate:.0f}%) "
               f"with model {ai.OLLAMA_MODEL}, {args.repeat} run(s) per scenario")
    print("\n" + summary)
    if not args.only:
        REPORT.write_text("# Last eval run\n\n" + summary + "\n\n"
                          "| Scenario | Passed | Avg time |\n|---|---|---|\n"
                          + "\n".join(rows) + "\n")
        print(f"Report saved to {REPORT}")


if __name__ == "__main__":
    main()
