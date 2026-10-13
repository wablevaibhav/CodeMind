"""Run the evaluation suite against locally installed Ollama model(s).

    python scripts/run_eval.py                      # configured model
    python scripts/run_eval.py --models a:7b b:3b   # compare models side by side
    python scripts/run_eval.py --json

Schema validity and heuristic property checks are reported separately; neither proves that an
answer is technically correct.
"""

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings  # noqa: E402
from app.evaluation import load_cases, run_evaluation  # noqa: E402
from app.main import build_service  # noqa: E402


def evaluate(model: str) -> dict:
    settings = get_settings().model_copy(update={"ollama_model": model})
    service, ollama, _, _ = build_service(settings)
    try:
        if not ollama.has_model(model):
            return {"model": model, "error": "not installed"}
        started = time.monotonic()
        report = run_evaluation(service, load_cases())
        report["model"] = model
        report["total_seconds"] = round(time.monotonic() - started, 1)
        report["avg_seconds_per_case"] = round(report["total_seconds"] / max(report["cases"], 1), 1)
        report["avg_attempts"] = round(
            sum(r["attempts"] for r in report["results"]) / max(report["cases"], 1), 2
        )
        return report
    finally:
        ollama.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="single model (default: OLLAMA_MODEL)")
    parser.add_argument("--models", nargs="+", help="compare several models")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    models = args.models or [args.model or get_settings().ollama_model]
    reports = [evaluate(m) for m in models]

    if args.json:
        print(json.dumps(reports, indent=2))
        return 0

    header = (
        f"{'model':<22}{'schema':>8}{'1st-try':>9}{'props':>8}{'att':>6}{'s/case':>8}{'total s':>9}"
    )
    print(header)
    print("-" * len(header))
    for r in reports:
        if "error" in r:
            print(f"{r['model']:<22}  {r['error']}")
            continue
        print(
            f"{r['model']:<22}{r['schema_validity_rate']:>8.0%}{r['first_attempt_valid_rate']:>9.0%}"
            f"{r['property_pass_rate']:>8.0%}{r['avg_attempts']:>6}"
            f"{r['avg_seconds_per_case']:>8}{r['total_seconds']:>9}"
        )
    print("\nprops = heuristic content checks (not correctness). att = average attempts per case.")
    for r in reports:
        for c in r.get("results", []):
            if c["error"] or c["property_failures"]:
                print(
                    f"  {r['model']}: {c['id']} {c['error'] or ''} {'; '.join(c['property_failures'])}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
