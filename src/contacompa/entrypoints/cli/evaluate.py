"""Run: python -m contacompa.entrypoints.cli.evaluate --set path.json --baseline path.json."""

import argparse
import json
from pathlib import Path

from contacompa.application.evaluation import gate, score


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--write-baseline", type=Path)
    parser.add_argument("--private", action="store_true")
    args = parser.parse_args()
    cases = json.loads(args.set.read_text())
    if args.private:
        keys = [case.get("document_key") for case in cases]
        if len(keys) < 9 or any(not isinstance(key, str) or not key for key in keys):
            parser.error("private release set requires nine labeled cases with document_key")
        if len(set(keys)) != len(keys):
            parser.error("private release set contains duplicate document_key values")
    metrics = score(cases)
    print(json.dumps(metrics, indent=2, sort_keys=True))
    if args.write_baseline:
        args.write_baseline.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n")
    if args.private and not args.baseline:
        baseline = metrics
    elif args.baseline:
        baseline = json.loads(args.baseline.read_text())
    else:
        baseline = None
    failures = gate(metrics, baseline, private=args.private) if baseline else []
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
