"""Comparable rules-only and one-call reviews using identical evidence guards."""
from home_review.checks import apply_clarifications
from home_review.evidence import (accept_facts, accept_issues, accept_statements, calculate,
                                  packet_sources, rules_issues)
from home_review.models import DemoCase, Fact, Limits, SingleCallReview


def finish_baseline(case: DemoCase, limits: Limits, facts: dict, issues: list,
                    sources: dict, answers: dict, errors: list, model_calls: int,
                    statements: dict | None = None) -> dict:
    initial_checks, _ = calculate(case, facts, issues, limits)
    requested = initial_checks["current"]["unresolved"]
    applicable = {field: answers.get(field) for field in requested}
    confirmed = [field for field, value in applicable.items() if value is not None and value != ""]
    revised = apply_clarifications([Fact.model_validate(f) for f in facts["current"]], applicable, requested)
    facts = {**facts, "current": [f.model_dump(mode="json") for f in revised]}
    checks, catalog = calculate(case, facts, issues, limits, confirmed)
    brief = {}
    for name, items in (statements or {}).items():
        brief[name], rejected = accept_statements(items, sources, initial_checks, limits.model_dump(mode="json"),
            issues, questions=(name == "questions"))
        errors.extend(rejected)
    return {"checks": checks, "initial_checks": initial_checks, "initial_unresolved": requested,
            "facts": facts, "issues": issues, "findings": catalog, "errors": errors,
            "brief": brief, "model_calls": model_calls, "search_count": 0, "local_count": 0,
            "read_sources": sources, "coverage_complete": True, "confirmed": confirmed,
            "clarification_used": bool(requested),
            "brief_stage": "Before reviewer clarification; no extra model call is made."}


def rules_review(case: DemoCase, limits: Limits, answers: dict) -> dict:
    sources = packet_sources(case)
    facts, _ = accept_facts(case, [], sources)
    issues = rules_issues(sources)
    return finish_baseline(case, limits, facts, issues, sources, answers, [], 0)


def single_call_review(case: DemoCase, limits: Limits, answers: dict, model) -> dict:
    sources = packet_sources(case)
    parsed, _ = accept_facts(case, [], sources)
    starting_checks, _ = calculate(case, parsed, [], limits)
    result = model.complete("single_call", SingleCallReview, {
        "sources": sources, "limits": limits.model_dump(mode="json"), "rules_derived_checks": starting_checks,
    })
    facts, errors = accept_facts(case, result.facts, sources)
    issues, rejected = accept_issues(result.issues, sources)
    errors.extend(rejected)
    return finish_baseline(case, limits, facts, issues, sources, answers, errors, 1,
                           {"observations": result.observations, "questions": result.questions})
