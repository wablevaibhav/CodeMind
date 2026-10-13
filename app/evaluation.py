"""Small evaluation harness.

Two things are measured *separately*:
  * schema validity  - did the pipeline return a contract-valid response (objective);
  * property checks  - heuristic expectations on the content (keywords, code present, ...).
Property checks are NOT a proof of technical correctness; review answers yourself.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.exceptions import CodeMindError
from app.schemas.requests import Mode, UserQuestion
from app.schemas.responses import AssistantResult
from app.services.assistant_service import AssistantService

DEFAULT_CASES = Path(__file__).resolve().parent.parent / "tests" / "eval" / "cases.json"


@dataclass
class CaseResult:
    id: str
    schema_valid: bool
    attempts: int
    property_failures: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def properties_ok(self) -> bool:
        return self.schema_valid and not self.property_failures


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def check_properties(result: AssistantResult, expect: dict[str, Any]) -> list[str]:
    r = result.response
    text = " ".join(
        [r.answer, r.summary or "", *r.steps, *r.concepts, *(cb.code for cb in r.code_blocks)]
    ).lower()
    failures: list[str] = []
    for group in expect.get("any_of", []):
        if not any(k.lower() in text for k in group):
            failures.append(f"none of {group} found")
    for kw in expect.get("all_of", []):
        if kw.lower() not in text:
            failures.append(f"missing {kw!r}")
    if len(r.code_blocks) < expect.get("min_code_blocks", 0):
        failures.append(f"expected >= {expect['min_code_blocks']} code block(s)")
    lang = expect.get("code_language")
    if lang and not any(cb.language.lower() == lang.lower() for cb in r.code_blocks):
        failures.append(f"no {lang} code block")
    if "status" in expect and r.status.value != expect["status"]:
        failures.append(f"status {r.status.value} != {expect['status']}")
    return failures


def run_evaluation(service: AssistantService, cases: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[CaseResult] = []
    for case in cases:
        q = UserQuestion(mode=Mode(case.get("mode", "ask")), **case["input"])
        try:
            res = service.answer(q)
        except CodeMindError as exc:
            results.append(
                CaseResult(
                    case["id"], False, exc.retry.attempts if exc.retry else 0, error=exc.code
                )
            )
            continue
        results.append(
            CaseResult(
                case["id"],
                True,
                res.retry.attempts,
                property_failures=check_properties(res, case.get("expect", {})),
            )
        )
    total = len(results) or 1
    return {
        "cases": len(results),
        "schema_validity_rate": sum(r.schema_valid for r in results) / total,
        "property_pass_rate": sum(r.properties_ok for r in results) / total,
        "first_attempt_valid_rate": sum(r.schema_valid and r.attempts == 1 for r in results)
        / total,
        "results": [r.__dict__ for r in results],
    }
