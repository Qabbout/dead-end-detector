"""Behavioral checks against normalized observations, not model internals."""
from copy import deepcopy
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "check.py"


def event(**changes):
    value = dict(goal="auth", context="env-v1", approach="timeout",
                 hypothesis="request-times-out", outcome="failure",
                 progress=False, failure="auth-401")
    value.update(changes)
    if value["outcome"] != "failure":
        value.pop("failure", None)
    return value


def encoded(events):
    return b"".join((json.dumps(e) + "\n").encode() for e in events)


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Event checker is missing")
        spec = importlib.util.spec_from_file_location("dead_end_check", SCRIPT)
        self.check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.check)

    def assertDecision(self, events, status, reason):
        result = self.check.classify(events)
        self.assertEqual((result["status"], result["reason"]), (status, reason))
        return result

    def test_empty_history_is_uncertain(self):
        self.assertDecision([], "insufficient-evidence", "empty-window")

    def test_one_failure_is_suspect(self):
        self.assertDecision([event()], "suspect", "observed-failure")

    def test_two_equivalent_failures_require_reassessment(self):
        result = self.assertDecision([event(), event()], "suspect", "repeated-failure")
        self.assertEqual(result["next_action"], "reassess-approach")

    def test_three_equivalent_failures_stop_same_approach(self):
        result = self.assertDecision([event()] * 3, "stalled", "repeated-failure")
        self.assertEqual(result["next_action"], "change-hypothesis")

    def test_success_without_evidence_is_not_progress(self):
        values = [event(outcome="success") for _ in range(4)]
        self.assertDecision(values, "stalled", "no-progress-window")

    def test_two_nonprogress_searches_are_suspect(self):
        self.assertDecision([event(outcome="success")] * 2,
                            "suspect", "no-progress-warning")

    def test_one_nonprogress_search_is_uncertain(self):
        self.assertDecision([event(outcome="success")],
                            "insufficient-evidence", "short-window")

    def test_alternating_failed_approaches_are_stalled(self):
        a, b = event(), event(approach="refresh-token", hypothesis="token-expired")
        self.assertDecision([a, b, a, b], "stalled", "approach-cycle")

    def test_four_distinct_unproductive_attempts_are_stalled(self):
        values = [event(approach=f"search-{i}", failure=f"error-{i}") for i in range(4)]
        self.assertDecision(values, "stalled", "no-progress-window")

    def test_four_nonprogress_attempts_override_two_equivalent_failure_warning(self):
        a, b = event(), event(approach="refresh-token", hypothesis="token-expired")
        result = self.assertDecision([a, a, b, b], "stalled", "no-progress-window")
        self.assertEqual(result["next_action"], "reassess-approach")

    def test_failure_signature_change_is_not_equivalent_failure(self):
        self.assertDecision([event(), event(failure="connection-refused")],
                            "suspect", "observed-failure")

    def test_hypothesis_change_is_not_equivalent_failure(self):
        self.assertDecision([event(), event(hypothesis="token-expired")],
                            "suspect", "observed-failure")

    def test_failure_with_new_evidence_is_reported_progress(self):
        values = [event(progress=True, evidence=f"narrowed-fixture-{i}") for i in range(6)]
        result = self.assertDecision(values, "progressing", "reported-progress")
        self.assertEqual(result["counts"]["without_progress"], 0)

    def test_unsupported_evidence_is_caller_reported_not_verified(self):
        result = self.assertDecision(
            [event(), event(progress=True, evidence="unverified-claim")],
            "progressing", "reported-progress")
        self.assertEqual(result["next_action"], "continue")

    def test_progress_resets_retry_history(self):
        values = [event()] * 3 + [event(progress=True, evidence="new-cause")] + [event()]
        self.assertDecision(values, "suspect", "observed-failure")

    def test_context_change_resets_history(self):
        result = self.assertDecision([event(), event(), event(context="env-v2")],
                                     "suspect", "observed-failure")
        self.assertEqual(result["counts"]["comparable"], 1)

    def test_latest_goal_is_not_mixed_with_earlier_goals(self):
        self.assertDecision([event()] * 4 + [event(goal="build", outcome="success")],
                            "insufficient-evidence", "short-window")

    def test_interleaved_goal_preserves_failure_comparison(self):
        result = self.assertDecision([event(), event(goal="other"), event()],
                                     "suspect", "repeated-failure")
        self.assertEqual(result["counts"],
                         {"window": 3, "comparable": 2, "without_progress": 2})

    def test_three_interleaved_equivalent_failures_stop_same_approach(self):
        result = self.assertDecision(
            [event(), event(goal="other"), event(), event(goal="other"), event()],
            "stalled", "repeated-failure")
        self.assertEqual(result["counts"],
                         {"window": 5, "comparable": 3, "without_progress": 3})

    def test_interleaved_goal_preserves_approach_cycle(self):
        a, b = event(), event(approach="refresh-token", hypothesis="token-expired")
        other = event(goal="other")
        result = self.assertDecision([a, other, b, other, a, other, b],
                                     "stalled", "approach-cycle")
        self.assertEqual(result["counts"]["comparable"], 4)

    def test_interleaved_goal_preserves_no_progress_window(self):
        values = []
        for i in range(4):
            if i:
                values.append(event(goal="other"))
            values.append(event(outcome="success", approach=f"search-{i}"))
        result = self.assertDecision(values, "stalled", "no-progress-window")
        self.assertEqual(result["counts"]["without_progress"], 4)

    def test_unrelated_events_do_not_reset_latest_goal(self):
        unrelated = [event(goal="other", progress=True, evidence="other-task-advanced"),
                     event(goal="other", outcome="waiting"),
                     event(goal="other", outcome="blocked", blocker="permission"),
                     event(goal="other", context="env-v2")]
        for other in unrelated:
            with self.subTest(event=other):
                result = self.assertDecision([event(), other, event()],
                                             "suspect", "repeated-failure")
                self.assertEqual(result["counts"]["comparable"], 2)

    def test_progress_reset_survives_interleaved_events(self):
        values = [event(), event(), event(goal="other"),
                  event(progress=True, evidence="new-cause"), event(goal="other"), event()]
        result = self.assertDecision(values, "suspect", "observed-failure")
        self.assertEqual(result["counts"],
                         {"window": 6, "comparable": 4, "without_progress": 1})

    def test_interleaved_goals_do_not_hide_context_changes(self):
        other = event(goal="other")
        cases = [([event(), other, event(context="env-v2"), other, event()],
                  "suspect", "observed-failure", 1),
                 ([event(), other, event(context="env-v2"), other,
                   event(context="env-v2")], "suspect", "repeated-failure", 2)]
        for values, status, reason, comparable in cases:
            with self.subTest(status=status):
                result = self.assertDecision(values, status, reason)
                self.assertEqual(result["counts"]["comparable"], comparable)

    def test_interleaved_history_remains_globally_bounded(self):
        values = [event()] + [event(goal="other")] * 31 + [event()]
        result = self.assertDecision(values, "suspect", "observed-failure")
        self.assertEqual(result["counts"],
                         {"window": 32, "comparable": 1, "without_progress": 1})

    def test_each_external_blocker_has_precise_reason(self):
        for blocker in ("permission", "missing-input", "external"):
            with self.subTest(blocker=blocker):
                self.assertDecision([event(), event(outcome="blocked", blocker=blocker)],
                                    "blocked", blocker)

    def test_waiting_takes_precedence_over_old_failures(self):
        self.assertDecision([event()] * 4 + [event(outcome="waiting")],
                            "waiting", "active-operation")

    def test_waiting_preserves_nonprogress_attempts(self):
        values = [event(outcome="success")] * 3 + [event(outcome="waiting"),
                                                  event(outcome="success")]
        result = self.assertDecision(values, "stalled", "no-progress-window")
        self.assertEqual(result["counts"]["without_progress"], 4)

    def test_waiting_preserves_equivalent_failures_for_bounded_retry(self):
        result = self.assertDecision([event(), event(), event(outcome="waiting"), event()],
                                     "stalled", "repeated-failure")
        self.assertEqual(result["counts"]["without_progress"], 3)

    def test_blocker_does_not_erase_equivalent_failures(self):
        result = self.assertDecision(
            [event(), event(), event(outcome="blocked", blocker="permission"), event()],
            "stalled", "repeated-failure")
        self.assertEqual(result["counts"]["without_progress"], 3)

    def test_resolved_blocker_with_new_context_resets_failures(self):
        result = self.assertDecision(
            [event(), event(), event(outcome="blocked", blocker="permission"),
             event(context="authorized-env-v2")], "suspect", "observed-failure")
        self.assertEqual(result["counts"]["comparable"], 1)

    def test_blocked_observations_do_not_create_a_false_stall_after_resume(self):
        blocked = event(outcome="blocked", blocker="permission")
        result = self.assertDecision([blocked] * 3 + [event(outcome="success")],
                                     "insufficient-evidence", "short-window")
        self.assertEqual(result["counts"]["without_progress"], 1)

    def test_reported_progress_takes_precedence_over_failures(self):
        self.assertDecision([event()] * 4 + [event(outcome="success", progress=True,
                                                  evidence="test-now-passes")],
                            "progressing", "reported-progress")

    def test_alternating_approaches_with_changed_errors_is_not_exact_cycle(self):
        values = [event(), event(approach="refresh"),
                  event(failure="error-new"), event(approach="refresh")]
        self.assertDecision(values, "stalled", "no-progress-window")

    def test_returning_to_old_context_does_not_join_history(self):
        result = self.assertDecision([event(), event(context="env-v2"), event()],
                                     "suspect", "observed-failure")
        self.assertEqual(result["counts"]["comparable"], 1)

    def test_bounded_history_for_large_iterable(self):
        result = self.check.classify(event(progress=True, evidence="narrowing") for _ in range(500))
        self.assertEqual(result["counts"]["window"], 32)

    def test_input_not_modified(self):
        values = [event(), event()]
        before = deepcopy(values)
        self.check.classify(values)
        self.assertEqual(values, before)

    def test_output_never_echoes_input_text(self):
        marker = "private-sensitive-marker"
        values = [event(goal=marker, hypothesis=marker, failure=marker)] * 2
        self.assertNotIn(marker, json.dumps(self.check.classify(values)))

    def test_rejects_invalid_schema_and_contradictions(self):
        invalid = [None, [], {}, event(progress=1), event(progress="false"),
                   event(goal=""), event(goal=" padded "), event(goal="x" * 257),
                   event(goal="control\nvalue"), event(goal=True), event(outcome="done"),
                   event(extra="secret"), event(progress=True),
                   event(outcome="waiting", progress=True, evidence="claim"),
                   event(outcome="blocked"), event(blocker="permission"),
                   event(outcome="blocked", blocker="unknown"),
                   event(evidence="unverified-claim")]
        missing = event()
        del missing["failure"]
        invalid.append(missing)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.check.classify([value])

    def test_stream_retains_last_32_but_validates_old_records(self):
        values = [event(outcome="success")] * 40
        self.assertEqual(len(self.check.read_events(BytesIO(encoded(values)))), 32)
        with self.assertRaises(ValueError):
            self.check.read_events(BytesIO(b'{"invalid":"old"}\n' + encoded(values)))

    def test_stream_rejects_blank_invalid_duplicate_and_nonfinite_records(self):
        bad = [b"\n", b"not-json\n", b"[]\n", b"\xff\n",
               b'{"goal":"x","goal":"y"}\n',
               b'{"progress":NaN}\n', b'{"progress":Infinity}\n']
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.check.read_events(BytesIO(value))

    def test_stream_rejects_oversized_line_and_total_input(self):
        for value in (b" " * 16385, encoded([event()]) * 7000):
            with self.subTest(length=len(value)):
                with self.assertRaises(ValueError):
                    self.check.read_events(BytesIO(value))

    def test_exact_line_and_input_limits_are_accepted(self):
        base = json.dumps(event()).encode()
        line = base + b" " * (16384 - len(base) - 1) + b"\n"
        self.assertEqual(len(line), 16384)
        self.assertEqual(len(self.check.read_events(BytesIO(line))), 1)
        data = line * 64
        self.assertEqual(len(data), 1024 * 1024)
        self.assertEqual(len(self.check.read_events(BytesIO(data))), 32)
        with self.assertRaises(ValueError):
            self.check.read_events(BytesIO(data + encoded([event()])))


class CliTests(unittest.TestCase):
    def runCli(self, data=b"", args=()):
        self.assertTrue(SCRIPT.is_file(), "Event checker is missing")
        return subprocess.run([sys.executable, str(SCRIPT), *args], input=data,
                              capture_output=True, timeout=5)

    def test_cli_stalled_is_advisory_success_exit(self):
        result = self.runCli(encoded([event()] * 3))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "stalled")
        self.assertEqual(result.stderr, b"")

    def test_cli_two_equivalent_failures_require_reassessment(self):
        result = self.runCli(encoded([event(), event()]))
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual((decision["status"], decision["reason"], decision["next_action"]),
                         ("suspect", "repeated-failure", "reassess-approach"))

    def test_cli_interleaved_goals_preserve_failure_history(self):
        result = self.runCli(encoded([event(), event(goal="other"), event()]))
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual((decision["status"], decision["reason"]),
                         ("suspect", "repeated-failure"))
        self.assertEqual(decision["counts"]["comparable"], 2)

    def test_cli_progress_is_explicitly_caller_reported(self):
        result = self.runCli(encoded([event(progress=True, evidence="unverified-claim")]))
        self.assertEqual(result.returncode, 0)
        decision = json.loads(result.stdout)
        self.assertEqual((decision["status"], decision["reason"]),
                         ("progressing", "reported-progress"))

    def test_cli_can_read_a_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "event summaries.jsonl"
            path.write_bytes(encoded([event(outcome="waiting")]))
            result = self.runCli(args=[str(path)])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "waiting")

    def test_cli_empty_stdin_is_uncertain(self):
        result = self.runCli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["status"], "insufficient-evidence")

    def test_cli_invalid_input_does_not_echo_or_decide(self):
        result = self.runCli(b"private-token-xyz\n")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"private-token-xyz", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_cli_missing_file_does_not_leak_path(self):
        result = self.runCli(args=["/missing/private-token-xyz.jsonl"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertNotIn(b"private-token-xyz", result.stderr)

    def test_cli_help_exits_successfully(self):
        result = self.runCli(args=["--help"])
        self.assertEqual(result.returncode, 0)
        self.assertIn(b"JSONL", result.stdout)

    def test_cli_rejects_extra_arguments_without_echoing(self):
        result = self.runCli(args=["private-marker", "extra"])
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"private-marker", result.stderr)


if __name__ == "__main__":
    unittest.main()
