import argparse
import json
from pathlib import Path
import sys

from . import History, check, verify
from .model import strict_json


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Bounded linearizability checks for completed get/put/cas histories."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    solve = commands.add_parser("check")
    solve.add_argument("history", type=Path)
    solve.add_argument("--max-states", type=int, default=50_000)
    solve.add_argument("--format", choices=["json", "text"], default="json")
    certificate = commands.add_parser("verify")
    certificate.add_argument("history", type=Path)
    certificate.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    try:
        history = History.load(args.history)
        if args.command == "verify":
            with args.report.open("rb") as source:
                data = source.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("certificate exceeds byte limit")
            verify(history, strict_json(data.decode("utf-8")))
            print(
                "VERIFIED: witness order, real-time constraints, returned values, and final state"
            )
            return 0
        report = check(history, max_states=args.max_states)
        if report["status"] == "linearizable":
            verify(history, report)
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f'{report["status"].upper()}: {report["operation_count"]} completed operations')
            print(
                f'Search: {report["stats"]["states_explored"]}/{args.max_states} states; '
                f'{report["stats"]["cache_hits"]} cached dead-state hits'
            )
            if report["witness"] is not None:
                print("Witness: " + (" -> ".join(report["witness"]) or "(empty)"))
                print("Final state: " + json.dumps(report["final_state"], sort_keys=True))
            elif report["status"] == "unknown":
                print("Budget exhausted. This is not a consistency violation.")
            else:
                print("No legal real-time-respecting order matches every observed result.")
        return {"linearizable": 0, "nonlinearizable": 1, "unknown": 3}[report["status"]]
    except (OSError, ValueError, RecursionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
