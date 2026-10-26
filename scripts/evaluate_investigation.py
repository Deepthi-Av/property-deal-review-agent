"""Evaluate identical frozen packets with rules, one model call, and the agent loop.

The optional injected failure is a separate resilience run, not a baseline benchmark.
Outputs retain per-case evidence and briefs for manual inspection; no gold is prompted.
"""
import argparse
import hashlib
import json
import time
from datetime import datetime, timezone

from home_review.baselines import rules_review, single_call_review
from home_review.evidence import LocalSourceTool
from home_review.fixtures import ROOT
from home_review.models import DemoCase, Limits
from home_review.providers import NebiusGateway, ProviderError, Settings, TavilyGateway
from home_review.workflow import ReviewService


class CountingModel:
    def __init__(self, delegate):
        self.delegate = delegate
        self.roles = []

    def complete(self, role, schema, payload):
        self.roles.append(role)
        return self.delegate.complete(role, schema, payload)


def score(report: dict, expected: dict) -> dict:
    actual = set(report["initial_unresolved"])
    required = set(expected["initial_unresolved"])
    issues = {(i["field"], i["kind"]) for i in report["issues"] if i["deal"] == "current"}
    required_issues = {tuple(i) for i in expected["required_issues"]}
    check = report["checks"]["current"]
    expected_status = "unknown" if expected["final_monthly_total"] is None else "pass"
    metrics = {
        "unresolved_fields_correct": actual == required,
        "unnecessary_question_fields": sorted(actual - required),
        "missed_question_fields": sorted(required - actual),
        "numeric_result_correct": (check["monthly_total"], check["monthly_status"]) == (expected["final_monthly_total"], expected_status),
        "required_context_found": required_issues <= issues,
        "context_hits": len(required_issues & issues), "context_expected": len(required_issues),
        "extra_context_categories": sorted([list(i) for i in issues - required_issues]),
        "required_sources_read": set(expected["required_sources"]) <= set(report["read_sources"]),
        "rejected_output_warnings": sum("Rejected" in e or "rejected" in e for e in report["errors"]),
    }
    metrics["case_pass"] = all(metrics[k] for k in (
        "unresolved_fields_correct", "numeric_result_correct", "required_context_found", "required_sources_read"))
    return metrics


def run_agent(case, limits, answers, model, search, fault=None):
    local = LocalSourceTool({fault["source_id"]: fault["failures"]} if fault else {})
    service = ReviewService(model, search, local)
    review_id = service.start(case, limits)
    initial = service.snapshot(review_id)
    if initial["state"].get("fatal"):
        raise ProviderError("Extraction failed; no usable agent report was produced.")
    requested = initial["pending"]["fields"] if initial["pending"] else []
    initial_checks = initial["state"]["checks"]
    if initial["pending"]:
        service.resume(review_id, {field: answers.get(field) for field in requested})
    final = service.snapshot(review_id)
    if final["pending"]:
        raise RuntimeError("Unexpected second human handoff")
    return {**final["state"]["report"], "initial_unresolved": requested,
            "initial_checks": initial_checks, "activity": final["state"]["activity"],
            "read_attempts": final["state"]["read_attempts"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modes", default="rules,single,agent")
    parser.add_argument("--case", default="all")
    parser.add_argument("--inject-failure", action="store_true")
    parser.add_argument("--label", default="evaluation")
    args = parser.parse_args()
    modes = args.modes.split(",")
    if any(m not in ("rules", "single", "agent") for m in modes):
        parser.error("Modes must be rules,single,agent")
    if args.inject_failure and modes != ["agent"]:
        parser.error("Failure injection is a separate --modes agent run.")
    records = json.loads((ROOT / "data/investigation-evaluation.json").read_text(encoding="utf-8"))
    if args.case != "all":
        records = [r for r in records if r["case"]["case_id"] == args.case]
        if not records:
            parser.error("Unknown case")
    settings = Settings.load()
    gateway = NebiusGateway(settings) if any(m != "rules" for m in modes) else None
    limits = Limits(monthly="3300", cash="95000")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = ROOT / "artifacts" / f"investigation-{run_id}.json"
    path.parent.mkdir(exist_ok=True)
    output = {"run_id": run_id, "label": args.label, "injected_failure": args.inject_failure,
        "dataset_sha256": hashlib.sha256((ROOT / "data/investigation-evaluation.json").read_bytes()).hexdigest(),
        "provider_code_sha256": hashlib.sha256((ROOT / "src/home_review/providers.py").read_bytes()).hexdigest(),
        "source_code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / "src/home_review").glob("*.py"))},
        "model": settings.nebius_model if gateway else None, "tavily_configured": bool(settings.tavily_key),
        "measurement_limits": "Synthetic, author-known cases. No human time study, pricing measurement or chatbot comparison.",
        "rows": []}
    for record in records:
        case = DemoCase.model_validate(record["case"])
        for mode in modes:
            started = time.perf_counter()
            model = CountingModel(gateway) if gateway else None
            row = {"case": case.case_id, "mode": mode}
            try:
                if mode == "rules":
                    report = rules_review(case, limits, record["reviewer_answers"])
                elif mode == "single":
                    report = single_call_review(case, limits, record["reviewer_answers"], model)
                else:
                    report = run_agent(case, limits, record["reviewer_answers"], model, TavilyGateway(settings),
                                       record.get("tool_fault") if args.inject_failure else None)
                row.update(metrics=score(report, record["expected"]), report=report)
            except Exception as exc:
                row.update(metrics={"case_pass": False}, failure=str(exc) if isinstance(exc, ProviderError) else type(exc).__name__)
            row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            row["model_roles"] = model.roles if model else []
            output["rows"].append(row)
            path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
            print(json.dumps({"case": case.case_id, "mode": mode, "metrics": row["metrics"],
                              "model_calls": len(row["model_roles"]), "seconds": row["elapsed_seconds"],
                              "failure": row.get("failure")}), flush=True)
    print("Results:", path, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
