"""A fixture-grounded rules baseline, not a comparison against chatbots."""

import json

from home_review.checks import audit_source_coverage, summarize_deal
from home_review.fixtures import ROOT, load_cases
from home_review.models import Limits


def main() -> None:
    expected = {"clean": ("3220.00", "pass"), "missing": (None, "unknown"),
                "conflict": (None, "unknown"), "over_limit": ("3920.00", "fail")}
    results = []
    for case in load_cases():
        facts, _ = audit_source_coverage(case.current, [])
        review = summarize_deal(case.current, facts, Limits(monthly="3300", cash="95000"), "N")
        actual = (review["monthly_total"], review["monthly_status"])
        results.append({"case": case.case_id, "monthly_total": actual[0], "status": actual[1], "matches_expected": actual == expected[case.case_id]})
    output = {"baseline": "prepared-format source parser and numeric rules; no model calls; expected labels used only for scoring", "cases": results,
              "matched": sum(row["matches_expected"] for row in results), "total": len(results)}
    (ROOT / "artifacts").mkdir(exist_ok=True)
    (ROOT / "artifacts" / "rules-evaluation.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()

