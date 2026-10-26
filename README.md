# Two-deal home-purchase review

A Week 3 prototype that reviews a new home purchase against a comparison deal and
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

## Review the scope and architecture

See [docs/scope-and-design.md](docs/scope-and-design.md) for the keep/cut table,
architecture diagram, system design, budget and limitations. The total working
budget remains 15 hours, including user review and submission work.

The owner has chosen to retain the three specialists for explicit responsibilities,
separate evaluation and the Week 3 multi-agent learning pattern. Current measurements
do not show an advantage over the single-call baseline. See the
[three-agent justification and future roadmap](docs/agent-justification-and-roadmap.md)
for the decision, honest submission wording, proposed extensions and evaluation gates.
Roadmap capabilities are not part of the current application.

## Run locally on Windows

Use the existing Python 3.13 installation. The current project already has its own
`.venv`; dependencies are locked in `uv.lock`. On another checkout:

```powershell
uv sync --native-tls
Copy-Item .env.example .env
```

Keep real credentials only in `.env`, which is ignored. Required for a live review:

```text
NEBIUS_API_KEY=<your private key>
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1/
NEBIUS_CHAT_MODEL=<your available, structured-output-capable model>
TAVILY_API_KEY=<optional until you are ready to test live guidance>
```

The local setup reuses the existing configured Nebius model. It does not switch to
another provider. Missing Nebius settings block the live-agent run. A missing Tavily
key does not prevent local document review; no live web evidence is claimed without
a successful lookup. The sidebar does not display a Connections section.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app\streamlit_app.py --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false
```

Open the local URL printed by Streamlit. In the sidebar, choose an example, enter the two limits,
and select **Review deals**. For a missing or conflicting amount, enter a confirmed
monthly amount (or leave it unknown), then continue. Use **Reset review** after
editing `.env` to reload provider settings. Keep the UI local for this submission.

The workspace uses the supplied indigo, lavender, peach, and coral palette.
Review results appear in comparison cards, followed by **Overview**, **Source evidence**,
and **Activity** tabs. Pending clarification appears above the comparison so it is
easy to find.
The native widget theme lives in `.streamlit/config.toml`; layout styles are in
`app/styles.css`. The redesign does not change the agent workflow or calculations.

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

## Tests and evaluation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\evaluate_rules.py
.\.venv\Scripts\python.exe scripts\evaluate_investigation.py --modes rules
```

The offline tests replace provider responses while exercising the real graph and
Streamlit interface. They do not establish live provider quality. The rules baseline
parses the same prepared source text without any model calls; its result is not evidence
of agent superiority over rules, ChatGPT, Claude, or specialist products. If rules
solve these simple inputs equally well, report that result instead of claiming an
agent benefit. The labels are used only to score the baseline and live checks.

Optional live checks (consume Nebius credits; only synthetic data is sent):

```powershell
.\.venv\Scripts\python.exe scripts\smoke_live.py --case clean
.\.venv\Scripts\python.exe scripts\smoke_live.py --case missing --confirm home_insurance=150
.\.venv\Scripts\python.exe scripts\smoke_live.py --case conflict --confirm hoa=175
.\.venv\Scripts\python.exe scripts\evaluate_investigation.py --modes rules,single,agent
.\.venv\Scripts\python.exe scripts\evaluate_investigation.py --modes agent --case evidence_placeholder --inject-failure
```

The current comparison and its limits are recorded in [docs/results.md](docs/results.md).
Both model approaches passed the four defined final case checks after disclosed
guard repairs; the extra agents did not beat the single-call baseline. Generated
prose still needs source review. Full and targeted live runs are retained in the
compact public [measurement record](docs/evaluation-results.json).

To create a local source archive for publication, excluding the environment and
checking for locally configured API-key values:

```powershell
.\.venv\Scripts\python.exe scripts\package_submission.py
```

`artifacts/` stores local test summaries and is ignored. Measured status belongs in
[docs/build-log.md](docs/build-log.md). Expected fixture labels are excluded from live
model prompts. Review snippets and field-label/amount checks reduce extraction errors
but do not prove that every semantic interpretation is correct.

Read [docs/evaluation-protocol.md](docs/evaluation-protocol.md) for the frozen packets,
baseline fairness, first-run failures and repair disclosure. Each run writes a new
timestamped JSON artifact. A case pass checks the defined amounts, required context
and handoff fields; it does not establish that every sentence of the draft is correct.

## Boundaries

- One clarification round, two Tavily attempts, six local-tool calls, eight coordinator
  model decisions and at most two risk-review passes. Local source reads get at most
  two attempts per source. Unread sources make the final report explicitly incomplete.
- No private borrower details are used in search queries; queries come from a fixed topic list.
- Only readable HTTPS CFPB/HUD content is accepted as reference guidance. Generated search
  answers and bare snippets are not substituted for source text.
- Failed search leaves an explicit gap. Model failures are sanitized and reported;
  deterministic fallback is identified rather than presented as a successful model decision.
- Checkpoints live in memory. Resetting the browser or restarting the server can lose state.
- Changing buyer inputs does not alter an already completed review; run a new review.
- Multiple-agent roles are an assignment design, not a claim that they improve accuracy.
- Text-based PDF uploads are bounded as described above. No OCR, property API,
  scraping, portfolio analysis, cloud hosting or saved user accounts.

## Submission work still belongs to the owner

Use [docs/submission.md](docs/submission.md) for the Google Doc draft and demo plan.
GitHub publication, recording a live video, and submitting the form are not completed
by creating this local application.

