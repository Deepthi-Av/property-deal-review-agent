# home-purchase review

A prototype that reviews a new home purchase against a comparison deal and
two buyer-defined limits. Synthetic examples are the default; users can also select
text-based PDFs from a session document library. Streamlit calls a UI-independent LangGraph
workflow using one configured Nebius model for three specialist roles and a bounded
coordinator. Tavily can retrieve reference guidance from CFPB/HUD.

The bounded redesign investigates source evidence before comparison. The coordinator
can read supplemental text or search the local packets; an evidence specialist can
identify a quoted amount as provisional based on its context. A separate risk reviewer
checks for overlooked context and drafts cited follow-up text. Python retains the
numeric calculations and enforces a human handoff for unresolved new-deal amounts.

This is not an appraisal, affordability assessment, mortgage approval, investment
recommendation, or legal-compliance service. The built-in examples and their
financial figures are synthetic; uploaded documents are labelled separately.
No warehouse or listing website is modified or scraped.

## Review uploaded PDFs

1. Change **Review source** from its default **Synthetic examples** to **Uploaded PDFs**.
2. Open **Upload and manage PDFs**, choose a **Document type**, select a PDF, and
   optionally enter the date printed on the document. Click **Add document**.
3. Inspect its extracted text in the session library. Page markers are preserved;
   PDF table layout may not be. Repeat to add other documents or updated revisions.
4. Choose the **New-deal document** in the sidebar. Choose another uploaded PDF
   as the **Comparison document**, or use the explicitly labelled synthetic saved
   comparison. Up to two supporting PDFs can be assigned to each uploaded deal.
5. Check the extracted text and confirm the figures are in USD. Set buying limits
   and click **Review deals**. This sends the selected extracted text to Nebius;
   adding or previewing a PDF alone does not make a model request.
6. Confirm unresolved amounts when asked. **Source evidence** identifies filenames,
   document types, dates and reviewer confirmations. Unknown dates remain unknown.

The library holds up to eight PDFs in the browser's server-side session. Each PDF
is limited to 10 MB, 12 pages and 12,000 extracted characters. Oversized inputs are
rejected rather than silently truncated. Image-only/scanned pages, encrypted PDFs,
and interactive forms are rejected with an explanation; OCR is not implemented.
Export forms as flattened, text-based PDFs before uploading. Text extraction does
not establish that a table's headings and amounts were associated correctly.

Uploaded bytes and extracted text are not written to the project by this feature.
Refresh or a server restart can lose the library. **Reset review** keeps the library
but clears the review; **Remove selected PDF** removes that PDF and clears the active
review. A revised file can be added and selected without changing a review already
in progress. The document type is metadata and never supplies a missing amount.

Only USD cost comparison is supported. Existing guards require explicit field
labels and periods, so some loan-document layouts will need human clarification.
The recorded four-case baseline scores apply to the synthetic examples, not to
arbitrary PDFs. No live-model accuracy result for uploaded PDFs is claimed.

## What the functions do

| File | Purpose |
|---|---|
| `models.py` | Typed contracts for facts, limits, agent actions and citations |
| `checks.py` | Validate quoted amounts, normalize annual values, calculate limits and differences |
| `providers.py` | Nebius structured output, sanitized failures and restricted Tavily Search |
| `workflow.py` | Specialist roles, bounded coordinator, same-session interruption/resume |
| `evidence.py` | Local source tools, shared quotation/context guards and evidence-aware checks |
| `baselines.py` | Rules-only and single-call comparisons using the same evidence and guards |
| `fixtures.py` | Load four original examples and four investigation evaluation packets |
| `documents.py` | Validate PDF uploads, extract page text and assemble selected review packets |
| `app/streamlit_app.py` | Inputs, clarification form, tables, evidence and activity display |

The financial calculations use Decimal. Explicit zero is valid evidence; absence
is unknown. Cash to close is read directly, so the down payment is not added twice.
An independent prepared-format audit recovers omitted labeled amounts and reports
that recovery. A live model initially omitted a conflicting HOA figure; this audit
was added so the omission cannot silently produce a complete monthly total.
The prepared inputs already itemize recurring costs separately to avoid adding an
escrow total again. Uploaded PDFs may not; extraction instructions prohibit using
combined payment or escrow totals as individual cost components. This remains a
bounded text-PDF prototype, not a general mortgage-document parser.
