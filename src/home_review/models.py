"""Small typed contracts shared by the agents, rules and interface."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FieldName = Literal[
    "purchase_price", "principal_interest", "property_tax", "home_insurance",
    "mortgage_insurance", "hoa", "cash_to_close",
]
Period = Literal["monthly", "annual", "once"]
Status = Literal["pass", "fail", "unknown"]

LABELS = {
    "purchase_price": "Purchase price",
    "principal_interest": "Principal and interest",
    "property_tax": "Property tax",
    "home_insurance": "Homeowners insurance",
    "mortgage_insurance": "Mortgage insurance",
    "hoa": "HOA fee",
    "cash_to_close": "Cash to close",
}
MONTHLY_FIELDS = (
    "principal_interest", "property_tax", "home_insurance", "mortgage_insurance", "hoa",
)
TOPICS = {
    "housing_costs": "CFPB total monthly mortgage payment property taxes insurance HOA costs",
    "escrow": "CFPB Loan Estimate taxes insurance assessments not escrowed",
    "cash_to_close": "CFPB Loan Estimate estimated cash to close closing costs down payment",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(StrictModel):
    field: FieldName
    amount: Decimal = Field(ge=0, le=100_000_000, allow_inf_nan=False)
    period: Period
    quote: str = Field(min_length=3, max_length=600)
    provenance: Literal["document", "reviewer"] = "document"
    source_id: str = "primary"


class Extraction(StrictModel):
    facts: list[Fact] = Field(max_length=30)


class SourceDocument(StrictModel):
    source_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,60}$")
    title: str = Field(max_length=200)
    as_of: date | None
    text: str = Field(min_length=1, max_length=12000)
    document_type: str = "Prepared example"


class Deal(StrictModel):
    deal_id: str
    name: str
    as_of: date | None
    source_text: str
    documents: list[SourceDocument] = Field(default_factory=list, max_length=2)
    source_kind: Literal["synthetic", "uploaded"] = "synthetic"
    document_type: str = "Prepared example"


class DemoCase(StrictModel):
    case_id: str
    title: str
    description: str
    is_synthetic: bool
    current: Deal
    saved: Deal
    expected_facts: dict[str, list[Fact]] = Field(default_factory=dict)


class Limits(StrictModel):
    monthly: Decimal = Field(gt=0, le=100_000_000, allow_inf_nan=False)
    cash: Decimal = Field(gt=0, le=100_000_000, allow_inf_nan=False)


class Finding(StrictModel):
    finding_id: str
    message: str
    evidence_ids: list[str]


class PolicyPlan(StrictModel):
    """The policy agent selects supported issues, rather than inventing rules."""

    finding_ids: list[str] = Field(max_length=12)
    guidance_topic: Literal["housing_costs", "escrow", "cash_to_close", "none"]


class Decision(StrictModel):
    action: Literal["search", "clarify", "finalize", "read_source", "search_local"]
    topic: Literal["housing_costs", "escrow", "cash_to_close", "none"] = "none"
    reason: str = Field(max_length=400)
    source_id: str = ""
    query: str = Field(default="", max_length=200)


class ComparisonPlan(StrictModel):
    """Only precomputed findings can become the final briefing."""

    finding_ids: list[str] = Field(max_length=12)


class SourcedFact(StrictModel):
    deal: Literal["current", "saved"]
    fact: Fact


class ContextIssue(StrictModel):
    deal: Literal["current", "saved"]
    field: FieldName
    kind: Literal["provisional", "exclusion", "conflict", "limitation"]
    source_id: str
    quote: str = Field(min_length=10, max_length=1200)
    explanation: str = Field(min_length=1, max_length=600)
    needs_confirmation: bool


class InvestigationPlan(StrictModel):
    facts: list[SourcedFact] = Field(default_factory=list, max_length=30)
    issues: list[ContextIssue] = Field(default_factory=list, max_length=12)
    guidance_topic: Literal["housing_costs", "escrow", "cash_to_close", "none"] = "none"


class CitedStatement(StrictModel):
    text: str = Field(min_length=1, max_length=600)
    source_refs: list[str] = Field(min_length=1, max_length=6)


class RiskBrief(StrictModel):
    issues: list[ContextIssue] = Field(default_factory=list, max_length=6)
    observations: list[CitedStatement] = Field(default_factory=list, max_length=4)
    questions: list[CitedStatement] = Field(default_factory=list, max_length=3)


class SingleCallReview(StrictModel):
    facts: list[SourcedFact] = Field(max_length=40)
    issues: list[ContextIssue] = Field(default_factory=list, max_length=12)
    observations: list[CitedStatement] = Field(default_factory=list, max_length=4)
    questions: list[CitedStatement] = Field(default_factory=list, max_length=3)


class Guidance(StrictModel):
    citation_id: str
    title: str
    url: str
    passage: str
    retrieved_at: str


class SearchOutcome(StrictModel):
    sources: list[Guidance] = Field(default_factory=list)
    error: str | None = None

