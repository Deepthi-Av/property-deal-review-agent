"""Bounded source investigation, deterministic checks and enforced human handoff."""
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from home_review.checks import apply_clarifications, validate_facts
from home_review.evidence import (LocalSourceTool, accept_facts, accept_issues,
                                  accept_statements, calculate, packet_sources)
from home_review.models import (Decision, DemoCase, Extraction, Fact, InvestigationPlan,
                                Limits, RiskBrief, SourcedFact, TOPICS)
from home_review.providers import ModelGateway, ProviderError, SearchGateway


class ReviewState(TypedDict, total=False):
    case: dict
    limits: dict
    facts: dict
    raw_facts: dict
    checks: dict
    catalog: list
    policy: dict
    decision: dict
    guidance: list
    errors: list[str]
    activity: list[str]
    search_count: int
    decisions: int
    clarification_used: bool
    model_calls: int
    fatal: bool
    report: dict
    read_sources: dict
    read_attempts: dict
    local_count: int
    local_results: dict
    issues: list
    confirmed: list[str]
    risk_rounds: int
    needs_recheck: bool
    brief: dict
    contributions: dict


class ReviewService:
    def __init__(self, model: ModelGateway, search: SearchGateway, local: LocalSourceTool | None = None):
        self.model, self.search, self.local = model, search, local or LocalSourceTool()
        graph = StateGraph(ReviewState)
        for name in ("extract", "calculate", "policy_review", "coordinate", "local_tool", "web_search",
                     "clarify", "risk_review", "assemble", "failed"):
            graph.add_node(name, getattr(self, "_" + name))
        graph.add_edge(START, "extract")
        graph.add_conditional_edges("extract", lambda s: "failed" if s.get("fatal") else "calculate")
        graph.add_edge("calculate", "policy_review")
        graph.add_edge("policy_review", "coordinate")
        graph.add_conditional_edges("coordinate", lambda s: {
            "search": "web_search", "read_source": "local_tool", "search_local": "local_tool",
            "clarify": "clarify", "finalize": "risk_review"}[s["decision"]["action"]])
        graph.add_edge("local_tool", "calculate")
        graph.add_edge("web_search", "coordinate")
        graph.add_edge("clarify", "calculate")
        graph.add_conditional_edges("risk_review", lambda s: "calculate" if s.get("needs_recheck") else "assemble")
        graph.add_edge("assemble", END)
        graph.add_edge("failed", END)
        self.graph = graph.compile(checkpointer=InMemorySaver())

    def start(self, case: DemoCase, limits: Limits) -> str:
        packet_sources(case)
        review_id = str(uuid4())
        self.graph.invoke({
            "case": case.model_dump(mode="json", exclude={"expected_facts"}),
            "limits": limits.model_dump(mode="json"), "facts": {}, "raw_facts": {},
            "errors": [], "activity": [], "guidance": [], "search_count": 0,
            "decisions": 0, "clarification_used": False, "model_calls": 0, "fatal": False,
            "read_sources": {}, "read_attempts": {}, "local_count": 0, "local_results": {},
            "issues": [], "confirmed": [], "risk_rounds": 0, "needs_recheck": False,
            "brief": {}, "contributions": {"investigator_facts": 0, "audit_facts": 0, "investigator_issues": 0, "risk_issues": 0},
        }, self._config(review_id))
        return review_id

    def resume(self, review_id: str, values: dict) -> None:
        snapshot = self.snapshot(review_id)
        if not snapshot["pending"]:
            raise ValueError("This review has no pending clarification.")
        apply_clarifications([], values, snapshot["pending"]["fields"])
        self.graph.invoke(Command(resume=values), self._config(review_id))

    def snapshot(self, review_id: str) -> dict:
        snap = self.graph.get_state(self._config(review_id))
        if not snap.values:
            raise ValueError("Review state is unavailable; start a new review.")
        pending = next((item.value for task in snap.tasks for item in task.interrupts), None)
        return {"state": dict(snap.values), "pending": pending}

    @staticmethod
    def _config(review_id: str) -> dict:
        return {"configurable": {"thread_id": review_id}, "recursion_limit": 85}

    @staticmethod
    def _case(state):
        return DemoCase.model_validate(state["case"])

    def _extract(self, state):
        case = self._case(state)
        sources = {key: value for key, value in packet_sources(case).items() if value["source_id"] == "primary"}
        raw, proposals, errors, calls = {}, [], [], 0
        try:
            for label in ("current", "saved"):
                deal = getattr(case, label)
                calls += 1
                result = self.model.complete("extraction", Extraction, {"name": deal.name,
                    "document_type": deal.document_type, "source_kind": deal.source_kind, "source_text": deal.source_text})
                valid, rejected = validate_facts(deal, result.facts)
                raw[label] = [f.model_dump(mode="json") for f in valid]
                proposals.extend(SourcedFact(deal=label, fact=f) for f in valid)
                errors.extend(rejected)
        except ProviderError as exc:
            return {"fatal": True, "errors": [*errors, str(exc)], "model_calls": calls}
        facts, warnings = accept_facts(case, proposals, sources)
        return {"facts": facts, "raw_facts": raw, "read_sources": sources, "errors": [*errors, *warnings],
                "model_calls": calls, "activity": ["Extraction read both primary packets; Python checked quotations and labeled-amount coverage."]}

    def _calculate(self, state):
        checks, catalog = calculate(self._case(state), state["facts"], state["issues"],
                                    Limits.model_validate(state["limits"]), state["confirmed"])
        return {"checks": checks, "catalog": catalog,
                "activity": [*state["activity"], "Python recalculated costs and PASS/FAIL/UNKNOWN checks."]}

    def _policy_review(self, state):
        errors = list(state["errors"])
        facts, issues = state["facts"], state["issues"]
        topic = "none"
        contributions = dict(state["contributions"])
        try:
            plan = self.model.complete("investigation", InvestigationPlan, {
                "read_sources": state["read_sources"], "existing_facts": facts, "existing_issues": issues,
                "checks": state["checks"], "reviewer_confirmed_fields": state["confirmed"],
            })
            proposals = [item for item in plan.facts if not (item.deal == "current" and item.fact.field in state["confirmed"])]
            facts, rejected = accept_facts(self._case(state), proposals, state["read_sources"], facts, audit=False)
            model_added = sum(len(facts[k]) - len(state["facts"][k]) for k in facts)
            before_audit = sum(len(items) for items in facts.values())
            audit_warnings = []
            # Sources were audited before handoff. Do not restore document amounts
            # deliberately replaced by a reviewer and call that an extraction error.
            if not state["clarification_used"]:
                facts, audit_warnings = accept_facts(self._case(state), [], state["read_sources"], facts)
            issues, rejected_issues = accept_issues(plan.issues, state["read_sources"], issues)
            errors.extend([*rejected, *rejected_issues, *audit_warnings])
            topic = plan.guidance_topic
            contributions["investigator_facts"] += model_added
            contributions["audit_facts"] += sum(len(items) for items in facts.values()) - before_audit
            contributions["investigator_issues"] += len(issues) - len(state["issues"])
        except ProviderError as exc:
            errors.extend([str(exc), "Investigation unavailable; source context has not been fully interpreted."])
        checks, catalog = calculate(self._case(state), facts, issues, Limits.model_validate(state["limits"]), state["confirmed"])
        return {"facts": facts, "issues": issues, "checks": checks, "catalog": catalog,
                "policy": {"guidance_topic": topic}, "errors": list(dict.fromkeys(errors)),
                "contributions": contributions, "model_calls": state["model_calls"] + 1,
                "activity": [*state["activity"], f"Evidence investigator: {len(issues)} contextual issue(s) recorded."]}

    def _coordinate(self, state):
        sources = packet_sources(self._case(state))
        unread = [key for key in sources if key not in state["read_sources"] and state["read_attempts"].get(key, 0) < 2]
        allowed = ["finalize"]
        if state["local_count"] < 6 and unread:
            allowed += ["read_source", "search_local"]
        unresolved = state["checks"]["current"]["unresolved"]
        if unresolved and not state["clarification_used"]:
            allowed.append("clarify")
        if (state["search_count"] < 2 and not state["guidance"] and state["policy"]["guidance_topic"] != "none"
            and not any("Tavily is not configured" in e or "HTTP 401" in e or "HTTP 403" in e for e in state["errors"])):
            allowed.append("search")
        errors, calls = list(state["errors"]), state["model_calls"]

        def fallback():
            if "read_source" in allowed and state["decisions"] < 8:
                return Decision(action="read_source", source_id=unread[0], reason="Read remaining evidence before handoff or completion.")
            return Decision(action="clarify" if "clarify" in allowed else "finalize", reason="Use a bounded fallback; preserve unresolved information.")

        if state["decisions"] >= 8:
            decision = fallback()
        else:
            try:
                calls += 1
                decision = self.model.complete("orchestrator", Decision, {
                    "allowed_actions": allowed, "policy": state["policy"], "current_unresolved": unresolved,
                    "clarification_used": state["clarification_used"], "search_attempts": state["search_count"],
                    "guidance": state["guidance"], "errors": state["errors"], "checks": state["checks"],
                    "unread_sources": {key: {k: v for k, v in sources[key].items() if k != "text"} for key in unread},
                    "local_tool_result": state["local_results"], "local_tool_calls": state["local_count"],
                })
                if (decision.action not in allowed or (decision.action == "search" and decision.topic not in TOPICS)
                    or (decision.action == "read_source" and decision.source_id not in unread)
                    or (decision.action == "search_local" and not decision.query.strip())):
                    raise ProviderError("An unsupported coordinator action was blocked.")
            except ProviderError as exc:
                errors.append(str(exc))
                decision = fallback()
        # The model cannot silently skip available evidence or a required human handoff.
        if decision.action in ("finalize", "clarify") and "read_source" in allowed and state["decisions"] < 8:
            decision = fallback()
        if decision.action == "finalize" and "clarify" in allowed:
            decision = Decision(action="clarify", reason="Required handoff: unresolved current-deal amounts need reviewer confirmation.")
        return {"decision": decision.model_dump(), "errors": errors, "model_calls": calls,
                "decisions": state["decisions"] + 1,
                "activity": [*state["activity"], f"Coordinator -> {decision.action}: {decision.reason}"]}

    def _local_tool(self, state):
        decision = state["decision"]
        sources = packet_sources(self._case(state))
        read, attempts, errors = dict(state["read_sources"]), dict(state["read_attempts"]), list(state["errors"])
        try:
            if decision["action"] == "read_source":
                key = decision["source_id"]
                attempts[key] = attempts.get(key, 0) + 1
                outcome = self.local.read(sources, key)
                if "source" in outcome:
                    if outcome["source"] != sources[key]:
                        raise ValueError("Source mismatch")
                    read[key] = outcome["source"]
            else:
                outcome = self.local.search(sources, decision["query"])
            if outcome.get("error"):
                errors.append(outcome["error"])
        except Exception:
            outcome = {"error": "Local evidence tool failed; retry or another source may be used."}
            errors.append(outcome["error"])
        return {"read_sources": read, "read_attempts": attempts, "local_results": outcome,
                "local_count": state["local_count"] + 1, "errors": errors,
                "activity": [*state["activity"], f"Local tool {decision['action']}: {decision.get('source_id') or decision.get('query')}; {'failed' if outcome.get('error') else 'completed'}."]}

    def _web_search(self, state):
        try:
            outcome = self.search.search(state["decision"]["topic"])
            sources = [s.model_dump() for s in outcome.sources]
            errors = [*state["errors"], *([outcome.error] if outcome.error else [])]
        except Exception:
            sources, errors = [], [*state["errors"], "Search tool failed unexpectedly; no web guidance was verified."]
        return {"guidance": sources, "errors": errors, "search_count": state["search_count"] + 1,
                "activity": [*state["activity"], f"Tavily attempt {state['search_count'] + 1}: {len(sources)} approved-domain passage(s)."]}

    def _clarify(self, state):
        fields = state["checks"]["current"]["unresolved"]
        context = [i for i in state["issues"] if i["deal"] == "current" and i["field"] in fields]
        answer = interrupt({"fields": fields, "context": context,
            "question": "Confirm unresolved new-deal amounts, or leave them unknown. Recurring amounts must be monthly USD; price and cash to close are one-time USD."})
        revised = apply_clarifications([Fact.model_validate(f) for f in state["facts"]["current"]], answer, fields)
        confirmed = [*state["confirmed"], *[field for field, value in answer.items() if value is not None and value != ""]]
        read_sources = dict(state["read_sources"])
        if confirmed:
            read_sources["current:reviewer"] = {"deal": "current", "source_id": "reviewer",
                "title": "Reviewer confirmation in this session; not an original document",
                "as_of": "current session", "text": "\n".join(f.quote for f in revised if f.provenance == "reviewer")}
        return {"facts": {**state["facts"], "current": [f.model_dump(mode="json") for f in revised]},
                "confirmed": list(dict.fromkeys(confirmed)), "read_sources": read_sources, "clarification_used": True,
                "activity": [*state["activity"], "Reviewer clarification recorded; blank answers remain unknown and source documents remain intact."]}

    def _risk_review(self, state):
        errors = list(state["errors"])
        issues = state["issues"]
        brief = {"observations": [], "questions": []}
        calls = state["model_calls"]
        if state["risk_rounds"] < 2:
            try:
                calls += 1
                result = self.model.complete("risk_review", RiskBrief, {
                    "read_sources": state["read_sources"], "checks": state["checks"], "catalog": state["catalog"],
                    "existing_issues": issues, "reviewer_confirmed_fields": state["confirmed"],
                })
                issues, rejected = accept_issues(result.issues, state["read_sources"], issues)
                errors.extend(rejected)
                revised_checks, _ = calculate(self._case(state), state["facts"], issues,
                    Limits.model_validate(state["limits"]), state["confirmed"])
                for key in brief:
                    brief[key], rejected = accept_statements(getattr(result, key), state["read_sources"], revised_checks,
                        state["limits"], issues, questions=(key == "questions"))
                    errors.extend(rejected)
            except ProviderError as exc:
                errors.append(str(exc))
        checks, catalog = calculate(self._case(state), state["facts"], issues, Limits.model_validate(state["limits"]), state["confirmed"])
        added = len(issues) - len(state["issues"])
        recheck = bool(checks["current"]["unresolved"] and not state["clarification_used"])
        contributions = {**state["contributions"], "risk_issues": state["contributions"]["risk_issues"] + added}
        return {"issues": issues, "checks": checks, "catalog": catalog, "brief": brief,
                "risk_rounds": state["risk_rounds"] + 1, "needs_recheck": recheck,
                "contributions": contributions, "model_calls": calls, "errors": errors,
                "activity": [*state["activity"], f"Risk review added {added} contextual issue(s); Python preserved the numeric findings."]}

    def _assemble(self, state):
        unread = sorted(set(packet_sources(self._case(state))) - set(state["read_sources"]))
        errors = list(dict.fromkeys(state["errors"]))
        if unread:
            errors.append("Some source documents were not read; evidence review is incomplete: " + ", ".join(unread))
        report = {"checks": state["checks"], "findings": state["catalog"], "issues": state["issues"],
            "brief": state["brief"], "guidance": state["guidance"], "errors": errors,
            "limits": state["limits"], "model_calls": state["model_calls"], "search_count": state["search_count"],
            "local_count": state["local_count"], "read_sources": state["read_sources"],
            "coverage_complete": not unread, "clarification_used": state["clarification_used"],
            "confirmed": state["confirmed"], "contributions": state["contributions"],
            "notice": "Synthetic prototype. Cost checks do not establish affordability, valuation or legal compliance. Source interpretations require review."}
        return {"report": report, "errors": errors}

    def _failed(self, state):
        return {"report": {"fatal": True, "errors": state["errors"], "model_calls": state["model_calls"]}}
