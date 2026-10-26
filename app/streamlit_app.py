"""Home-review workspace. Business rules and model calls live in home_review."""

from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path
from uuid import uuid4

import streamlit as st

from home_review.checks import money
from home_review.documents import (DOCUMENT_TYPES, MAX_DOCUMENTS, DocumentError,
                                   add_document, build_uploaded_case, extract_pdf)
from home_review.fixtures import load_cases, load_investigation_cases
from home_review.models import DemoCase, LABELS, Limits, MONTHLY_FIELDS
from home_review.providers import NebiusGateway, ProviderError, Settings, TavilyGateway
from home_review.workflow import ReviewService

st.set_page_config(page_title="Home Review · Deal workspace", page_icon="🏠", layout="wide", initial_sidebar_state="expanded")
st.markdown("<style>" + Path(__file__).with_name("styles.css").read_text(encoding="utf-8") + "</style>", unsafe_allow_html=True)

HOUSE_ICON = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m3 10 9-7 9 7M5 9v11h14V9M9 20v-7h6v7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'


def html(content: str) -> None:
    """Render controlled markup; dynamic source/model text must be escaped."""
    st.markdown(content, unsafe_allow_html=True)


def get_service() -> ReviewService:
    """One service per browser session: no cross-user checkpoint sharing."""
    if "review_service" not in st.session_state:
        settings = Settings.load()
        st.session_state.review_service = ReviewService(NebiusGateway(settings), TavilyGateway(settings))
    return st.session_state.review_service


def amount(value) -> str:
    return money(Decimal(value)) if value is not None else "Unknown"


def badge(status: str) -> str:
    style = status if status in ("pass", "fail", "unknown") else "neutral"
    return f'<span class="pill pill-{style}">{escape(status.upper())}</span>'


def quote(text: str) -> None:
    html(f'<blockquote class="source-quote">{escape(text)}</blockquote>')


def show_checks(checks: dict) -> None:
    columns = st.columns(2, gap="medium")
    for column, key, label in zip(columns, ("current", "saved"), ("NEW DEAL", "SAVED COMPARISON")):
        deal = checks[key]
        with column, st.container(key="deal_" + key):
            html(f'<div class="deal-top"><span class="deal-type">{label}</span><span class="deal-date">Source · {escape(deal["as_of"])}</span></div>')
            st.subheader(deal["name"])
            if deal.get("source_kind") == "uploaded":
                st.caption("Uploaded PDF · " + deal["document_type"])
            else:
                st.caption("Synthetic example")
            st.metric("Complete monthly total", amount(deal["monthly_total"]))
            st.caption(f"Known monthly subtotal: {amount(deal['known_subtotal'])}" + (" · incomplete until unresolved costs are confirmed." if deal["monthly_total"] is None else " · all monthly components included."))
            html(f'<div class="status-row"><span>Monthly buyer limit</span>{badge(deal["monthly_status"])}</div>')
            cash_value = amount(deal["rows"]["cash_to_close"]["value"])
            html(f'<div class="status-row"><span>Cash to close <strong>{escape(cash_value)}</strong></span>{badge(deal["cash_status"])}</div>')


def show_breakdown(checks: dict) -> None:
    st.subheader("Cost breakdown")
    st.caption("Annual costs are converted to monthly amounts. Purchase price and cash to close are one-time figures. All amounts are USD.")
    rows = []
    for field, label in LABELS.items():
        row = {"Figure": label + (" / month" if field in MONTHLY_FIELDS else "")}
        for key, heading in (("current", "New deal"), ("saved", "Saved deal")):
            item = checks[key]["rows"][field]
            row[heading] = amount(item["value"]) if item["value"] is not None else item["state"].capitalize()
        rows.append(row)
    st.dataframe(rows, hide_index=True, width="stretch")


def show_packets(case) -> None:
    with st.expander("Prepared source packets" if case.is_synthetic else "Review source packets"):
        st.caption("Source text used for this review. PDF text includes page markers; extraction may not preserve table layout. Reviewer answers are recorded separately.")
        for key in ("current", "saved"):
            deal = getattr(case, key)
            st.text(f"{deal.name} · primary packet")
            st.caption(f"{deal.document_type} · {'Synthetic example' if deal.source_kind == 'synthetic' else 'Uploaded PDF'} · document date {deal.as_of or 'Not provided'}")
            st.text(deal.source_text)
            for document in deal.documents:
                st.caption(f"{key}:{document.source_id} — {document.title} — {document.document_type} — {document.as_of or 'Not provided'}")
                st.text(document.text)


def clear_review() -> None:
    for key in ("review_service", "review_id", "review_error"):
        st.session_state.pop(key, None)
    st.session_state.form_nonce = str(uuid4())


def show_document_library(library: dict) -> None:
    with st.expander("Upload and manage PDFs", expanded=not library):
        st.caption("Text-based PDFs only · 10 MB · 12 pages · 12,000 extracted characters per file · up to 8 documents per session. Scans need OCR first.")
        with st.form("pdf_upload", clear_on_submit=True):
            document_type = st.selectbox("Document type", DOCUMENT_TYPES, index=None, placeholder="Choose the type of document", key="upload_document_type")
            uploaded = st.file_uploader("Upload a PDF", type=["pdf"], max_upload_size=10, key="pdf_upload_file")
            as_of = st.date_input("Document date (optional)", value=None, help="Use the date on the document. Leave blank if unknown; the upload date will not be substituted.")
            add_clicked = st.form_submit_button("Add document", type="primary")
        if add_clicked:
            if uploaded is None:
                st.error("Choose a PDF to add to your document library.")
            else:
                try:
                    document = extract_pdf(uploaded.getvalue(), uploaded.name, document_type, as_of)
                    added = add_document(library, document)
                    st.session_state.pdf_notice = "Document added. Select it in the sidebar and inspect its extracted text below." if added else "This document is already in your library."
                    st.rerun()
                except DocumentError as exc:
                    st.error(str(exc))
        if library:
            st.caption(f"{len(library)} of {MAX_DOCUMENTS} documents in this session. Uploading extracts text locally; it does not start an AI review.")
            preview_id = st.selectbox("Preview uploaded document", list(library), format_func=lambda key: library[key].label, key="pdf_preview_id")
            document = library[preview_id]
            st.caption(f"{document.page_count} page(s) · {document.document_type} · document date {document.as_of or 'Not provided'}")
            st.text(document.text)
            st.download_button("Download original PDF", data=document.pdf_bytes, file_name=document.filename, mime="application/pdf", key="download_uploaded_pdf")
            if st.button("Remove selected PDF", help="Removes this document from the library and clears the active review."):
                del library[preview_id]
                clear_review()
                st.rerun()


def show_empty() -> None:
    html('''<div class="empty-hero"><div><div class="eyebrow">A little more clarity. A better next step.</div>
      <h2>Two deals.<br>One clear picture.</h2><p>Bring the costs, source evidence, and unanswered questions into one view. Choose a synthetic example or upload your PDFs, then set your limits to begin.</p></div>
      <svg class="hero-art" viewBox="0 0 300 240" fill="none" role="img" aria-label="Illustration of two homes being compared">
      <circle cx="155" cy="118" r="100" fill="#E3DEED"/><circle cx="231" cy="48" r="24" fill="#F0C38E"/>
      <rect x="32" y="83" width="123" height="130" rx="13" fill="#D4CEDF" transform="rotate(-7 32 83)"/>
      <rect x="42" y="71" width="123" height="130" rx="13" fill="#FFF" transform="rotate(-7 42 71)"/>
      <path d="m63 124 33-32 39 25" stroke="#48426D" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="m71 119 5 39 53-6-5-40" fill="#EEEBF4" stroke="#48426D" stroke-width="3" stroke-linejoin="round"/>
      <path d="m94 154-2-22 14-2 3 23" fill="#F0C38E"/><path d="m73 176 61-7" stroke="#D4CEDF" stroke-width="5" stroke-linecap="round"/>
      <rect x="142" y="101" width="123" height="130" rx="13" fill="#C9C1D7" transform="rotate(6 142 101)"/>
      <rect x="146" y="90" width="123" height="130" rx="13" fill="#48426D" transform="rotate(6 146 90)"/>
      <path d="m164 143 37-26 34 34" stroke="#F0C38E" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="m171 139-4 39 54 6 4-40" fill="#625B84" stroke="#F0C38E" stroke-width="3" stroke-linejoin="round"/>
      <path d="m186 180 2-22 14 2-2 22" fill="#F1AA9B"/><path d="m164 195 61 6" stroke="#9288AB" stroke-width="5" stroke-linecap="round"/>
      <circle cx="65" cy="54" r="13" fill="#F1AA9B"/><path d="m28 151-6 8m8 7-10 2" stroke="#B3A7C4" stroke-width="2" stroke-linecap="round"/>
      </svg></div>
      <div class="feature-grid">
      <div class="feature"><div class="feature-icon">01</div><h3>Compare the real totals</h3><p>See monthly costs and cash to close against your own limits.</p></div>
      <div class="feature"><div class="feature-icon">02</div><h3>Follow the evidence</h3><p>Inspect the source quotations behind figures and findings.</p></div>
      <div class="feature"><div class="feature-icon">03</div><h3>Keep the final say</h3><p>Confirm unresolved amounts, or leave them unknown.</p></div>
      </div>''')


settings = Settings.load()
cases = load_cases() + load_investigation_cases()
library = st.session_state.setdefault("uploaded_documents", {})
case = None
with st.sidebar:
    html(f'<div class="brand"><div class="brand-mark">{HOUSE_ICON}</div><div><div class="brand-name">Home Review</div><div class="brand-sub">A clearer next move</div></div></div><div class="sidebar-label">Workspace</div><div class="workspace-nav">{HOUSE_ICON}<span>Deal review</span><span>●</span></div>')
    st.subheader("Set up your review")
    st.caption("Choose examples or documents from your session library.")
    review_source = st.selectbox("Review source", ["Synthetic examples", "Uploaded PDFs"], key="review_source")
    selection_error = None
    if review_source == "Uploaded PDFs":
        if library:
            current_id = st.selectbox("New-deal document", list(library), format_func=lambda key: library[key].label, key="current_pdf_id")
            saved_id = st.selectbox("Comparison document", [None, *library], format_func=lambda key: "Synthetic saved comparison (default)" if key is None else library[key].label, key="saved_pdf_id")
            with st.expander("Supporting documents (optional)"):
                current_support = st.multiselect("New-deal supporting PDFs", list(library), format_func=lambda key: library[key].label, max_selections=2, key="current_support_ids")
                saved_support = st.multiselect("Comparison supporting PDFs", list(library), format_func=lambda key: library[key].label, max_selections=2, disabled=saved_id is None, key="saved_support_ids")
            try:
                case = build_uploaded_case(library, current_id, saved_id, cases[0].saved, current_support, saved_support if saved_id else [])
            except DocumentError as exc:
                selection_error = str(exc)
                st.error(selection_error)
            if saved_id is None:
                st.caption("Your comparison uses synthetic figures until you select another uploaded PDF.")
        else:
            st.info("Add a PDF in Upload and manage PDFs to begin.")
    with st.form("review_inputs"):
        if review_source == "Synthetic examples":
            selected_id = st.selectbox("New-deal example", [c.case_id for c in cases], format_func=lambda key: next(c.title for c in cases if c.case_id == key), key="synthetic_case_id")
            case = next(c for c in cases if c.case_id == selected_id)
        html('<div class="sidebar-label">Your buying limits · USD</div>')
        monthly = st.number_input("Monthly housing limit (USD)", min_value=1.0, max_value=100000000.0, value=3300.0, step=50.0)
        cash = st.number_input("Cash-to-close limit (USD)", min_value=1.0, max_value=100000000.0, value=95000.0, step=1000.0)
        pdf_confirmed = True
        if review_source == "Uploaded PDFs":
            confirmation_key = "pdf_checked_" + "_".join([current_id, str(saved_id), *current_support, *(saved_support if saved_id else [])]) if library else "pdf_checked_empty"
            pdf_confirmed = st.checkbox("I checked the extracted text; these figures are in USD.", key=confirmation_key)
            st.caption("Review deals sends the selected extracted text to Nebius for AI review. PDF tables may need manual confirmation.")
        submitted = st.form_submit_button("Review deals", type="primary", width="stretch", disabled=case is None)
    st.caption("Use Review deals to apply changes and start a new review.")
    st.divider()
    reset_review = st.button("Reset review", width="stretch", help="Clear this review and reload provider settings. Your session document library is kept.")
    st.caption("PDFs and review progress stay in this session. Browser refresh or a server restart can lose them. Reset review keeps your document library.")

if reset_review:
    clear_review()
    st.rerun()

display_case = case
if not submitted and "review_id" in st.session_state:
    display_case = DemoCase.model_validate(get_service().snapshot(st.session_state.review_id)["state"]["case"])
review_label = "SYNTHETIC DEMO" if display_case and display_case.is_synthetic else "PDF WORKSPACE"
if display_case and not display_case.is_synthetic:
    review_label = "PDF REVIEW · SYNTHETIC COMPARISON" if display_case.saved.source_kind == "synthetic" else "UPLOADED PDF REVIEW"
html(f'<div class="topline"><div class="breadcrumb">Workspace<span>/</span>Home purchase</div><span class="pill pill-neutral">{review_label}</span></div>')
st.title("A clearer view of your next home.")
html('<p class="intro">Compare the costs. Follow the evidence. Know what still needs an answer.</p>')
if not settings.nebius_key or not settings.nebius_model:
    st.error("Add NEBIUS_API_KEY and NEBIUS_CHAT_MODEL to this project's .env, then use Reset review before running a live review.")

if review_source == "Uploaded PDFs":
    if st.session_state.get("pdf_notice"):
        st.success(st.session_state.pop("pdf_notice"))
    show_document_library(library)

if submitted and not pdf_confirmed:
    st.error("Inspect the extracted text and confirm the figures are in USD before starting the PDF review.")
elif submitted and case is not None:
    st.session_state.pop("review_id", None)
    try:
        with st.spinner("Reviewing source evidence and buyer limits…"):
            service = get_service()
            review_id = service.start(case, Limits(monthly=str(monthly), cash=str(cash)))
        st.session_state.review_id = review_id
        st.session_state.pop("review_error", None)
    except ProviderError as exc:
        st.session_state.review_error = str(exc)
    except Exception:
        st.session_state.review_error = "The review could not complete. Reset and retry; check provider configuration if the problem persists."

if st.session_state.get("review_error"):
    st.error(st.session_state.review_error)

state, pending = {}, None
if "review_id" in st.session_state:
    snapshot = get_service().snapshot(st.session_state.review_id)
    state, pending = snapshot["state"], snapshot["pending"]
report = state.get("report")
stage = 2 if pending else 3 if report and not report.get("fatal") else 1
step_labels = ("Set your limits", "Review & clarify", "Compare findings")
steps = []
for i, label in enumerate(step_labels, 1):
    current_class = " current" if i == stage else ""
    current_attr = ' aria-current="step"' if i == stage else ""
    steps.append(f'<div class="workflow-step{current_class}"{current_attr}><span class="step-number">{i:02}</span>{label}</div>')
html('<div class="workflow-strip" aria-label="Review stages">' + "".join(steps) + "</div>")

if not state:
    show_empty()
    if case is not None:
        st.subheader("Your selected example" if case.is_synthetic else "Your selected documents")
        st.text(case.title)
        st.caption(case.description.replace("$", r"\$"))
        show_packets(case)
else:
    active_case = DemoCase.model_validate(state["case"])
    html('<div class="section-label">Active review</div>')
    st.subheader(state["case"]["title"])
    st.caption(f"Limits used for this review: monthly {amount(state['limits']['monthly'])}; cash {amount(state['limits']['cash'])}. Submit Review deals to apply changed limits.".replace("$", r"\$"))
    if pending:
        with st.container(key="clarification"):
            html('<span class="pill pill-unknown">YOUR INPUT NEEDED</span>')
            st.subheader("Reviewer clarification")
            st.write(pending["question"])
            for issue in pending.get("context", []):
                st.write(issue["explanation"].replace("$", r"\$"))
                quote(issue["quote"])
                st.caption(f"Source: {issue['deal']}:{issue['source_id']}")
            with st.form("clarification_" + st.session_state.review_id):
                answers = {field: st.text_input(LABELS[field] + (" (monthly USD)" if field in MONTHLY_FIELDS else " (one-time USD)"), key="answer_" + st.session_state.review_id + field) for field in pending["fields"]}
                st.caption("Only enter an amount you can confirm. A blank answer stays unknown; original documents are preserved.")
                resume = st.form_submit_button("Continue review; blank values stay unknown", type="primary")
            if resume:
                try:
                    parsed = {field: Decimal(value.replace(",", "").strip()) if value.strip() else None for field, value in answers.items()}
                    with st.spinner("Applying your clarification and finishing the review…"):
                        get_service().resume(st.session_state.review_id, parsed)
                    st.rerun()
                except (InvalidOperation, ValueError):
                    st.error("Enter non-negative amounts as numbers, or leave the field blank. Only unresolved fields can be confirmed.")
                except ProviderError as exc:
                    st.error(str(exc))
    if state.get("checks"):
        show_checks(state["checks"])
    for error in dict.fromkeys(state.get("errors", [])):
        st.warning(error)
    overview, sources, activity = st.tabs(["Overview", "Source evidence", "Activity"])
    with overview:
        if state.get("checks"):
            show_breakdown(state["checks"])
        if report and not report.get("fatal"):
            if not report.get("coverage_complete", True):
                st.warning("Some source documents could not be reviewed. Treat this report as incomplete.")
            if report.get("issues"):
                st.subheader("Source context")
            for issue in report.get("issues", []):
                confirmed = issue["deal"] == "current" and issue["field"] in report.get("confirmed", [])
                st.write(issue["explanation"].replace("$", r"\$"))
                quote(issue["quote"])
                st.caption(f"{issue['deal']}:{issue['source_id']} · {'Reviewer confirmed a value' if confirmed else 'Source interpretation for review'}")
            for heading, key in (("Review observations", "observations"), ("Follow-up questions", "questions")):
                statements = report.get("brief", {}).get(key, [])
                if statements:
                    st.subheader(heading)
                    for statement in statements:
                        st.write(statement["text"].replace("$", r"\$"))
                        st.caption("Sources: " + ", ".join(statement["source_refs"]))
            st.subheader("Review findings")
            for index, finding in enumerate(report["findings"], 1):
                refs = '<div class="evidence-ref">Evidence: ' + escape(", ".join(finding["evidence_ids"])) + '</div>' if finding["evidence_ids"] else ""
                html(f'<div class="finding"><span class="finding-marker">{index}</span><div class="finding-text">{escape(finding["message"])}{refs}</div></div>')
        elif pending:
            st.info("This comparison is provisional. Answer the clarification above to finish the review; blank amounts will remain unknown.")
    with sources:
        st.subheader("Evidence behind the review")
        st.caption("Check the original quotations and distinguish document facts from reviewer confirmations.")
        show_packets(active_case)
        if report and not report.get("fatal"):
            with st.expander("Source evidence", expanded=True):
                for check in report["checks"].values():
                    for citation_id, evidence in check["citations"].items():
                        st.markdown(f"**{citation_id}: {evidence['deal']} — {evidence['field']}**")
                        quote(evidence["quote"])
                        st.caption(f"{evidence['provenance']} · {evidence.get('source_title', evidence['source_id'])} · {evidence.get('document_type', '')} · source date {evidence['as_of']}")
            with st.expander("Official reference guidance"):
                if not report["guidance"]:
                    st.write("No live web guidance was verified for this review.")
                for source in report["guidance"]:
                    st.link_button(source["title"], source["url"])
                    quote(source["passage"])
                    st.caption(f"{source['citation_id']} · retrieved {source['retrieved_at']}. This passage does not verify a property's actual costs.")
    with activity:
        st.subheader("Review activity")
        st.caption("Follow the workflow's recorded actions, tool results, and human handoff. An entry records execution; check any warnings for failures.")
        if report and not report.get("fatal"):
            html(f'<div class="report-stats"><span>Model requests <strong>{report["model_calls"]}</strong></span><span>Local tool calls <strong>{report.get("local_count", 0)}</strong></span><span>Tavily attempts <strong>{report["search_count"]}</strong></span><span>Reviewer clarification <strong>{"used" if report["clarification_used"] else "not used"}</strong></span></div>')
        for index, entry in enumerate(state.get("activity", []), 1):
            html(f'<div class="activity-entry"><span class="activity-index">{index:02}</span><div class="activity-text">{escape(entry)}</div></div>')

html('<div class="footer-note">Synthetic examples or uploaded PDF text · USD figures only · Session-only progress<br>This prototype checks supplied costs and completeness. It does not establish affordability, market value, legal compliance, or investment suitability.</div>')

