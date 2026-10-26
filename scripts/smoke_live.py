"""Optional live acceptance check. Uses only prepared synthetic documents."""

import argparse
import json
import time
from pathlib import Path

from home_review.fixtures import ROOT, load_cases
from home_review.checks import apply_clarifications, summarize_deal
from home_review.models import Fact, Limits
from home_review.providers import NebiusGateway, ProviderError, Settings, TavilyGateway
from home_review.workflow import ReviewService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", default="clean", choices=[c.case_id for c in load_cases()])
    parser.add_argument("--confirm", action="append", default=[], metavar="FIELD=MONTHLY_AMOUNT")
    args = parser.parse_args()
    started = time.perf_counter()
    case = next(c for c in load_cases() if c.case_id == args.case)
    try:
        settings = Settings.load()
        service = ReviewService(NebiusGateway(settings), TavilyGateway(settings))
        review_id = service.start(case, Limits(monthly="3300", cash="95000"))
        snapshot = service.snapshot(review_id)
        extracted = snapshot["state"].get("raw_facts", {})
        recovered = sum(len(snapshot["state"].get("facts", {}).get(key, [])) - len(extracted.get(key, [])) for key in ("current", "saved"))
        extraction_misses, extraction_extras = 0, 0
        for label in ("current", "saved"):
            expected = {(f.field, f.amount, f.period) for f in case.expected_facts[label]}
            actual = {(f.field, f.amount, f.period) for f in (Fact.model_validate(item) for item in extracted.get(label, []))}
            extraction_misses += len(expected - actual)
            extraction_extras += len(actual - expected)
        answers = {}
        if snapshot["pending"] and args.confirm:
            answers = dict(item.split("=", 1) for item in args.confirm)
            service.resume(review_id, answers)
            snapshot = service.snapshot(review_id)
        state = snapshot["state"]
        report = state.get("report", {})
        result = {
            "case": case.case_id,
            "status": "needs_clarification" if snapshot["pending"] else "failed" if report.get("fatal") else "degraded" if state["errors"] else "complete",
            "monthly_total": state.get("checks", {}).get("current", {}).get("monthly_total"),
            "monthly_status": state.get("checks", {}).get("current", {}).get("monthly_status"),
            "pending": snapshot["pending"], "model_calls": state["model_calls"],
            "search_attempts": state["search_count"], "errors": state["errors"],
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "tavily_configured": bool(settings.tavily_key),
            "extraction_missed_labeled_facts": extraction_misses,
            "extraction_extra_facts": extraction_extras,
            "source_audit_recovered_facts": recovered,
        }
        expected_facts = case.expected_facts["current"]
        if answers:
            allowed = summarize_deal(case.current, expected_facts, Limits(monthly="3300", cash="95000"), "N")["unresolved"]
            expected_facts = apply_clarifications(expected_facts, answers, allowed)
        expected_check = summarize_deal(case.current, expected_facts, Limits(monthly="3300", cash="95000"), "N")
        result["checks_match_labeled_facts"] = (result["monthly_total"], result["monthly_status"]) == (expected_check["monthly_total"], expected_check["monthly_status"])
        (ROOT / "artifacts").mkdir(exist_ok=True)
        (ROOT / "artifacts" / f"live-{case.case_id}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 1 if report.get("fatal") else 0
    except (ProviderError, ValueError) as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

