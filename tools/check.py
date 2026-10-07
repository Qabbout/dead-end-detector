#!/usr/bin/env python3
"""Advisory detection over bounded, explicitly supplied event summaries.

Author: Abdulrahman Qabbout. Runtime: Python 3.10+ standard library only.
"""
from collections import deque
import json
import sys
import unicodedata


WINDOW = 32
MAX_INPUT_BYTES = 1024 * 1024
MAX_LINE_BYTES = 16 * 1024
MAX_TEXT = 256
REQUIRED = {"goal", "context", "approach", "hypothesis", "outcome", "progress"}
OPTIONAL = {"failure", "blocker", "evidence"}
OUTCOMES = {"success", "failure", "blocked", "waiting"}
BLOCKERS = {"permission", "missing-input", "external"}


def validate_event(value):
    """Reject ambiguous observations; diagnostics never contain supplied text."""
    if type(value) is not dict or not REQUIRED <= value.keys():
        raise ValueError("invalid event schema")
    if value.keys() - (REQUIRED | OPTIONAL):
        raise ValueError("unknown event field")
    for key, text in value.items():
        if key == "progress":
            if type(text) is not bool:
                raise ValueError("progress must be boolean")
        elif (type(text) is not str or not text or len(text) > MAX_TEXT
              or text != text.strip()
              or any(unicodedata.category(c).startswith("C") for c in text)):
            raise ValueError("invalid text field")
    outcome = value["outcome"]
    if outcome not in OUTCOMES:
        raise ValueError("invalid outcome")
    if (outcome == "failure") != ("failure" in value):
        raise ValueError("failure signature required only for failures")
    if (outcome == "blocked") != ("blocker" in value):
        raise ValueError("blocker required only for blocked events")
    if outcome == "blocked" and value["blocker"] not in BLOCKERS:
        raise ValueError("invalid blocker")
    if value["progress"] != ("evidence" in value):
        raise ValueError("evidence required only for reported progress")
    if value["progress"] and outcome not in {"success", "failure"}:
        raise ValueError("blocked or waiting cannot report progress")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite JSON value")


def read_events(stream):
    """Read binary JSONL incrementally, validating even discarded old records."""
    events = deque(maxlen=WINDOW)
    total = 0
    while True:
        line = stream.readline(MAX_LINE_BYTES + 1)
        if not line:
            break
        total += len(line)
        if len(line) > MAX_LINE_BYTES or total > MAX_INPUT_BYTES:
            raise ValueError("input limit exceeded")
        if not line.strip():
            raise ValueError("blank event line")
        try:
            value = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
        except (UnicodeError, ValueError, RecursionError) as error:
            raise ValueError("invalid JSON record") from error
        events.append(validate_event(value))
    return list(events)


def _signature(event):
    return event["approach"], event["hypothesis"], event["failure"]


def classify(events):
    """Classify the latest comparable segment; never infer hidden progress."""
    window = deque(maxlen=WINDOW)
    for event in events:
        window.append(validate_event(event))
    counts = {"window": len(window), "comparable": 0, "without_progress": 0}

    def decision(status, reason, action):
        return {"status": status, "reason": reason, "next_action": action,
                "counts": counts}

    if not window:
        return decision("insufficient-evidence", "empty-window", "gather-evidence")

    latest = window[-1]
    segment = []
    for event in reversed(window):
        if event["goal"] != latest["goal"]:
            continue
        if event["context"] != latest["context"]:
            break
        segment.append(event)
    segment.reverse()
    counts["comparable"] = len(segment)
    recent = []
    for event in segment:
        if event["progress"]:
            recent.clear()
        elif event["outcome"] not in {"waiting", "blocked"}:
            recent.append(event)
    counts["without_progress"] = len(recent)

    if latest["outcome"] == "blocked":
        return decision("blocked", latest["blocker"], "report-dependency")
    if latest["outcome"] == "waiting":
        return decision("waiting", "active-operation", "wait-bounded")
    if latest["progress"]:
        return decision("progressing", "reported-progress", "continue")

    if (len(recent) >= 3
            and all(e["outcome"] == "failure" for e in recent[-3:])
            and len({_signature(e) for e in recent[-3:]}) == 1):
        return decision("stalled", "repeated-failure", "change-hypothesis")
    if len(recent) >= 4 and all(e["outcome"] == "failure" for e in recent[-4:]):
        a, b, c, d = (_signature(e) for e in recent[-4:])
        if a == c and b == d and a != b:
            return decision("stalled", "approach-cycle", "change-hypothesis")
    if len(recent) >= 4:
        return decision("stalled", "no-progress-window", "reassess-approach")
    if (len(recent) >= 2
            and all(e["outcome"] == "failure" for e in recent[-2:])
            and _signature(recent[-1]) == _signature(recent[-2])):
        return decision("suspect", "repeated-failure", "reassess-approach")
    if latest["outcome"] == "failure":
        return decision("suspect", "observed-failure", "probe-hypothesis")
    if len(recent) >= 2:
        return decision("suspect", "no-progress-warning", "probe-hypothesis")
    return decision("insufficient-evidence", "short-window", "gather-evidence")


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args in (["--help"], ["-h"]):
        print("Usage: python3 check.py [EVENTS.jsonl|-]\n"
              "Read bounded JSONL from a file or stdin; print an advisory JSON decision.\n"
              "Valid decisions exit 0; invalid/unreadable input exits 2. No API calls.")
        return 0
    if len(args) > 1 or (args and args[0].startswith("-") and args[0] != "-"):
        print("error: expected an optional JSONL path or stdin", file=sys.stderr)
        return 2
    try:
        if args and args[0] != "-":
            with open(args[0], "rb") as stream:
                events = read_events(stream)
        else:
            events = read_events(sys.stdin.buffer)
        result = classify(events)
    except (OSError, ValueError, RecursionError):
        print("error: invalid, oversized, or unreadable event input; see the event contract",
              file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
