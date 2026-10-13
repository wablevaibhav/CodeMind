from app.evaluation import load_cases, run_evaluation
from tests.conftest import valid_json


def test_eval_separates_schema_validity_from_properties(make_service):
    cases = [
        {"id": "good", "input": {"question": "q"}, "expect": {"all_of": ["comprehension"]}},
        {"id": "valid-but-wrong", "input": {"question": "q"}, "expect": {"all_of": ["zebra"]}},
        {"id": "invalid", "input": {"question": "q"}, "expect": {}},
    ]
    script = [
        valid_json(answer="Use a list comprehension"),
        valid_json(answer="Use a loop"),
        "bad",
        "bad",
        "bad",
    ]
    service, _ = make_service(script)
    report = run_evaluation(service, cases)
    assert report["cases"] == 3
    assert abs(report["schema_validity_rate"] - 2 / 3) < 1e-9
    assert abs(report["property_pass_rate"] - 1 / 3) < 1e-9
    by_id = {r["id"]: r for r in report["results"]}
    assert (
        by_id["valid-but-wrong"]["schema_valid"] and by_id["valid-but-wrong"]["property_failures"]
    )
    assert by_id["invalid"]["error"] == "generation_failed"


def test_bundled_cases_are_well_formed():
    cases = load_cases()
    assert len(cases) >= 5
    assert all("id" in c and "input" in c for c in cases)
