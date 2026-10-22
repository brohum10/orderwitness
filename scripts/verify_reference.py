"""Recompute every checked-in result; fail on changed semantics or search counters."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orderwitness import History, check, verify


def reproduce():
    result = {}
    for name in ("overlap-pass", "double-cas-fail", "stale-read-fail"):
        subject = History.load(ROOT / "examples" / f"{name}.json")
        report = check(subject)
        if report["status"] == "linearizable":
            verify(subject, report)
        result[name] = report
    result["budget-one"] = check(History.load(ROOT / "examples/overlap-pass.json"), max_states=1)
    # All writes overlap. Any permutation leaves x=0, so a later read of 1 is impossible.
    subject = History.from_document(
        {
            "version": 1,
            "operations": [
                dict(
                    id=f"write-{i}",
                    client=f"client-{i}",
                    start=i,
                    end=i + 8,
                    op="put",
                    key="x",
                    value=0,
                    result=None,
                )
                for i in range(8)
            ]
            + [dict(id="read", client="reader", start=16, end=17, op="get", key="x", result=1)],
        }
    )
    result["eight-overlapping-writes"] = check(subject)
    return result


def main():
    actual = reproduce()
    expected = json.loads((ROOT / "evidence/reference.json").read_text(encoding="utf-8"))
    if expected != actual:
        raise SystemExit(
            "Reference mismatch: inspect the changed results before updating evidence."
        )
    for name, report in actual.items():
        print(
            f'{name}: {report["status"]}; states={report["stats"]["states_explored"]}; '
            f'cache_hits={report["stats"]["cache_hits"]}'
        )
    print("All five reference results reproduced; positive witness independently verified.")


if __name__ == "__main__":
    main()
