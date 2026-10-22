import copy
import itertools
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from orderwitness import History, check, verify
from orderwitness.model import MAX_BYTES, strict_json

ROOT = Path(__file__).resolve().parents[1]


def operation(identifier="a", kind="get", start=0, end=1, key="x", result=None, **extra):
    return dict(
        id=identifier,
        client=identifier,
        op=kind,
        start=start,
        end=end,
        key=key,
        result=result,
        **extra,
    )


def history(*ops, initial=None):
    return History.from_document(dict(version=1, initial=initial or {}, operations=list(ops)))


def fixture(name="overlap-pass"):
    return History.load(ROOT / "examples" / f"{name}.json")


def brute_force(document):
    """Small independent oracle: permutations + direct typed-value replay, no search helpers."""
    operations = document["operations"]
    for ordered in itertools.permutations(operations):
        ranks = {op["id"]: i for i, op in enumerate(ordered)}
        if any(
            a["end"] < b["start"] and ranks[a["id"]] >= ranks[b["id"]]
            for a in operations
            for b in operations
        ):
            continue
        values = dict(document["initial"])
        valid = True
        for op in ordered:
            before = values.get(op["key"])
            if op["op"] == "get":
                if (type(before), before) != (type(op["result"]), op["result"]):
                    valid = False
                    break
            elif op["op"] == "put":
                values[op["key"]] = op["value"]
            else:
                success = (type(before), before) == (type(op["expected"]), op["expected"])
                if success != op["result"]:
                    valid = False
                    break
                if success:
                    values[op["key"]] = op["value"]
        if valid:
            return True
    return False


class ModelTests(unittest.TestCase):
    def test_normalization_ignores_row_order(self):
        doc = fixture().document()
        doc["operations"].reverse()
        self.assertEqual(History.from_document(doc).sha256, fixture().sha256)

    def test_history_detaches_mutable_input_and_output(self):
        doc = fixture().document()
        subject = History.from_document(doc)
        doc["initial"]["seat"] = 9
        subject.document()["initial"]["seat"] = 8
        self.assertEqual(subject.document()["initial"]["seat"], 0)

    def test_version_is_exact_integer(self):
        for value in (True, 1.0, 2, "1", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                History.from_document(dict(version=value, operations=[]))

    def test_unknown_fields_rejected(self):
        doc = fixture().document()
        doc["extra"] = 1
        with self.assertRaises(ValueError):
            History.from_document(doc)
        doc.pop("extra")
        doc["operations"][0]["extra"] = 1
        with self.assertRaises(ValueError):
            History.from_document(doc)

    def test_pending_calls_rejected(self):
        for field in ("end", "result"):
            doc = fixture().document()
            del doc["operations"][0][field]
            with self.assertRaises(ValueError):
                History.from_document(doc)

    def test_duplicate_operation_ids(self):
        with self.assertRaises(ValueError):
            history(operation(), operation(start=2, end=3))

    def test_invalid_names(self):
        for field in ("id", "client", "key"):
            for value in ("", "has spaces", 1, "x" * 129):
                op = operation()
                op[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    history(op)

    def test_event_positions(self):
        for start, end in ((1, 1), (2, 1), (-1, 1), (False, 1), (0, 1.5), (0, 2**63)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                history(operation(start=start, end=end))

    def test_global_event_ties_rejected(self):
        with self.assertRaises(ValueError):
            history(operation(), operation("b", start=1, end=2))

    def test_sequential_client_cannot_overlap(self):
        a, b = operation(end=3), operation("b", start=1, end=2)
        b["client"] = "a"
        with self.assertRaises(ValueError):
            history(a, b)
        b["start"], b["end"] = 4, 5
        history(a, b)

    def test_unsupported_scalar_types(self):
        for value in ([], {}, 1.0, float("inf"), 2**63, -(2**63) - 1, "x" * 1025):
            with self.subTest(value=repr(value)[:30]), self.assertRaises(ValueError):
                history(operation(result=value))

    def test_operation_specific_fields(self):
        invalid = [
            operation(kind="delete"),
            operation(kind="put", value=1, result=True),
            operation(kind="cas", expected=0, value=1, result=1),
            operation(kind="cas", expected=[], value=1, result=False),
            operation(kind="put"),
            operation(value=1),
        ]
        for op in invalid:
            with self.subTest(op=op), self.assertRaises(ValueError):
                history(op)

    def test_operation_and_key_limits(self):
        with self.assertRaises(ValueError):
            history(*(operation(str(i), start=2 * i, end=2 * i + 1) for i in range(33)))
        with self.assertRaises(ValueError):
            history(initial={str(i): None for i in range(33)})

    def test_strict_json_keys_and_numbers(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                strict_json(text)

    def test_load_byte_limit_and_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "history.json"
            for contents in (b" " * (MAX_BYTES + 1), b"\xff"):
                path.write_bytes(contents)
                with self.assertRaises(ValueError):
                    History.load(path)


class SearchTests(unittest.TestCase):
    def test_empty_history_is_valid(self):
        subject = history(initial={"untouched": True})
        report = check(subject, max_states=1)
        self.assertEqual(report["witness"], [])
        self.assertEqual(report["final_state"], {"untouched": True})
        self.assertTrue(verify(subject, report))

    def test_overlapping_cas_completion_order_is_not_effect_order(self):
        subject = fixture()
        report = check(subject)
        self.assertEqual(report["witness"], ["winner", "loser", "observe"])
        self.assertTrue(verify(subject, report))

    def test_two_successful_cas_cannot_both_claim_same_old_value(self):
        report = check(fixture("double-cas-fail"))
        self.assertEqual(report["status"], "nonlinearizable")
        self.assertIsNone(report["witness"])

    def test_stale_read_after_completed_write(self):
        report = check(fixture("stale-read-fail"))
        self.assertEqual(report["status"], "nonlinearizable")
        self.assertEqual(report["stats"]["real_time_edges"], 1)

    def test_overlapping_read_may_observe_old_value(self):
        subject = history(
            operation("write", "put", 0, 3, value=1), operation("read", start=1, end=2)
        )
        self.assertEqual(check(subject)["witness"], ["read", "write"])

    def test_failed_cas_does_not_mutate(self):
        subject = history(
            operation(kind="cas", expected=9, value=2, result=False),
            operation("read", start=2, end=3, result=1),
            initial={"x": 1},
        )
        self.assertTrue(verify(subject, check(subject)))

    def test_values_are_type_sensitive(self):
        for initial, result in ((True, 1), (1, True), (1, "1"), (None, "null")):
            with self.subTest(initial=initial, result=result):
                subject = history(operation(result=result), initial={"x": initial})
                self.assertEqual(check(subject)["status"], "nonlinearizable")
        subject = history(
            operation(kind="cas", expected=1, value=9, result=False), initial={"x": True}
        )
        self.assertTrue(verify(subject, check(subject)))

    def test_keys_are_independent(self):
        subject = history(
            operation("a", "put", value=1),
            operation("b", start=2, end=3, key="other"),
            initial={"unused": "ok"},
        )
        self.assertEqual(check(subject)["final_state"], {"x": 1, "other": None, "unused": "ok"})

    def test_budget_is_unknown_not_failure(self):
        report = check(fixture(), max_states=1)
        self.assertEqual(report["status"], "unknown")
        self.assertEqual(report["stats"]["states_explored"], 1)
        self.assertIsNone(report["witness"])
        self.assertIsNone(report["final_state"])

    def test_bad_budgets(self):
        for budget in (0, -1, True, 1.5, 1_000_001):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                check(history(), max_states=budget)

    def test_same_done_set_can_have_different_register_states(self):
        # a,b is a dead end, but b,a reaches x=1 with the same completed-operation set.
        subject = history(
            operation("a", "put", 0, 3, value=1),
            operation("b", "put", 1, 2, value=2),
            operation("read", start=4, end=5, result=1),
        )
        report = check(subject)
        self.assertEqual(report["witness"], ["b", "a", "read"])
        self.assertTrue(verify(subject, report))

    def test_dead_state_memo_hits(self):
        subject = history(
            *(operation(str(i), "put", i, i + 4, value=0) for i in range(4)),
            operation("bad", start=8, end=9, result=1),
        )
        report = check(subject)
        self.assertEqual(report["status"], "nonlinearizable")
        self.assertGreater(report["stats"]["cache_hits"], 0)

    def test_400_seeded_histories_against_independent_permutation_oracle(self):
        rng = random.Random(20261022)
        statuses = set()
        for case in range(400):
            n = rng.randrange(1, 7)
            positions = list(range(2 * n))
            rng.shuffle(positions)
            ops = []
            for i in range(n):
                start, end = sorted(positions[2 * i : 2 * i + 2])
                kind = rng.choice(["get", "put", "cas"])
                value = rng.choice([None, 0, 1, True, "1"])
                extra = {} if kind == "get" else {"value": value}
                if kind == "cas":
                    extra["expected"] = rng.choice([None, 0, 1, True])
                result = (
                    value if kind == "get" else None if kind == "put" else rng.choice([False, True])
                )
                ops.append(
                    operation(
                        str(i), kind, start, end, key=rng.choice(["x", "y"]), result=result, **extra
                    )
                )
            subject = history(*ops, initial={"x": rng.choice([None, 0, True])})
            expected = brute_force(subject.document())
            report = check(subject)
            with self.subTest(case=case):
                self.assertEqual(
                    report["status"], "linearizable" if expected else "nonlinearizable"
                )
                if expected:
                    self.assertTrue(verify(subject, report))
            statuses.add(report["status"])
        self.assertEqual(statuses, {"linearizable", "nonlinearizable"})


class CertificateTests(unittest.TestCase):
    def test_rejects_non_positive_reports(self):
        for subject, kwargs in ((fixture("double-cas-fail"), {}), (fixture(), {"max_states": 1})):
            with self.assertRaises(ValueError):
                verify(subject, check(subject, **kwargs))

    def test_tampered_reports(self):
        subject = fixture()
        valid = check(subject)
        replacements = [
            ("version", True),
            ("version", 1.0),
            ("history_sha256", "bad"),
            ("witness", ["winner", "winner", "observe"]),
            ("witness", []),
            ("witness", ["loser", "winner", "observe"]),
            ("witness", ["winner", "observe", "loser"]),
            ("final_state", {"seat": True}),
            ("final_state", {}),
        ]
        for key, value in replacements:
            report = copy.deepcopy(valid)
            report[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                verify(subject, report)

    def test_changed_history_rejects_original_certificate(self):
        report = check(fixture())
        doc = fixture().document()
        doc["initial"]["another"] = 1
        with self.assertRaises(ValueError):
            verify(History.from_document(doc), report)

    def test_get_result_is_independently_replayed(self):
        subject = history(operation(result=1))
        fake = dict(
            version=1,
            status="linearizable",
            history_sha256=subject.sha256,
            witness=["a"],
            final_state={"x": None},
        )
        with self.assertRaises(ValueError):
            verify(subject, fake)


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "orderwitness", *map(str, args)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_check_exit_codes_and_json(self):
        for name, code in (("overlap-pass", 0), ("double-cas-fail", 1), ("stale-read-fail", 1)):
            result = self.run_cli("check", ROOT / "examples" / f"{name}.json")
            self.assertEqual(result.returncode, code, result.stderr)
            self.assertIn("status", json.loads(result.stdout))

    def test_unknown_exit_code(self):
        result = self.run_cli("check", ROOT / "examples/overlap-pass.json", "--max-states", 1)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["status"], "unknown")

    def test_text_output(self):
        result = self.run_cli("check", ROOT / "examples/overlap-pass.json", "--format", "text")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("winner -> loser -> observe", result.stdout)

    def test_cli_certificate_and_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            report = check(fixture())
            path.write_text(json.dumps(report), encoding="utf-8")
            result = self.run_cli("verify", ROOT / "examples/overlap-pass.json", path)
            self.assertEqual(result.returncode, 0, result.stderr)
            path.write_text('{"version":true}', encoding="utf-8")
            result = self.run_cli("verify", ROOT / "examples/overlap-pass.json", path)
            self.assertEqual(result.returncode, 2)

    def test_cli_invalid_file_and_budget(self):
        for args in (
            ("check", ROOT / "not-present.json"),
            ("check", ROOT / "examples/overlap-pass.json", "--max-states", "0"),
        ):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("error:", result.stderr)


if __name__ == "__main__":
    unittest.main()
