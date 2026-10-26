"""PDF ingestion and real workflow checks using synthetic, in-memory documents."""
from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           NameObject, TextStringObject)

from home_review.documents import (DOCUMENT_TYPES, DocumentError, add_document,
                                   build_uploaded_case, extract_pdf)
from home_review.models import Limits
from home_review.workflow import ReviewService
from conftest import FakeSearch
from test_investigation import EvidenceModel


def pdf_bytes(texts, *, password=None, form=False):
    """Build small text PDFs in memory; no uploaded files or API calls are used."""
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        lines = ["BT /F1 10 Tf 40 740 Td 13 TL"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"({escaped}) Tj T*")
        lines.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(lines).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    if form:
        field = DictionaryObject({NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("amount"), NameObject("/V"): TextStringObject("100")})
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject([writer._add_object(field)])})
    if password:
        writer.encrypt(password)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_pdf_preserves_pages_text_type_and_optional_date():
    data = pdf_bytes(["Property tax: USD 6000 per year.", "HOA fee: USD 150 per month."])
    doc = extract_pdf(data, r"C:\private\worksheet.pdf", DOCUMENT_TYPES[0])
    assert doc.filename == "worksheet.pdf"
    assert doc.page_count == 2 and "[Page 2]" in doc.text
    assert "Property tax: USD 6000 per year." in doc.text
    assert doc.document_type == DOCUMENT_TYPES[0] and doc.as_of is None
    assert doc.pdf_bytes == data


@pytest.mark.parametrize("data, filename, error", [
    (b"not a PDF", "renamed.pdf", "valid PDF"),
    (b"%PDF-1.4 broken", "broken.pdf", "could not be read"),
    (b"%PDF-1.4", "wrong.txt", "valid PDF"),
    (b"%PDF-" + b" " * (10 * 1024 * 1024), "large.pdf", "exceeds 10 MB"),
], ids=["not-pdf", "corrupt", "wrong-extension", "oversize"])
def test_invalid_pdfs_are_rejected(data, filename, error):
    with pytest.raises(DocumentError, match=error):
        extract_pdf(data, filename, DOCUMENT_TYPES[0])


@pytest.mark.parametrize("options, texts, error", [
    ({"password": "test-password"}, ["Homeowners insurance: USD 150 per month."], "Password-protected"),
    ({}, [""], "no readable text"),
    ({}, ["Page with text", ""], "Page 2 has no readable text"),
    ({}, ["Text"] * 13, "1–12 pages"),
    ({"form": True}, ["A form"], "interactive form fields"),
])
def test_unsupported_pdfs_are_rejected(options, texts, error):
    with pytest.raises(DocumentError, match=error):
        extract_pdf(pdf_bytes(texts, **options), "input.pdf", DOCUMENT_TYPES[0])


def test_text_limit_rejects_whole_pdf_instead_of_truncating(monkeypatch):
    monkeypatch.setattr("home_review.documents.MAX_TEXT_CHARS", 60)
    with pytest.raises(DocumentError, match="no pages were silently discarded"):
        extract_pdf(pdf_bytes(["Amount " * 30]), "long.pdf", DOCUMENT_TYPES[0])


def test_document_type_is_required():
    with pytest.raises(DocumentError, match="Choose a document type"):
        extract_pdf(pdf_bytes(["Some text"]), "input.pdf", None)


def test_library_deduplicates_and_caps_documents(monkeypatch):
    monkeypatch.setattr("home_review.documents.MAX_DOCUMENTS", 1)
    data = pdf_bytes(["Property tax: USD 6000 per year."])
    doc = extract_pdf(data, "tax.pdf", DOCUMENT_TYPES[5])
    library = {}
    assert add_document(library, doc)
    assert not add_document(library, extract_pdf(data, "tax.pdf", DOCUMENT_TYPES[5]))
    with pytest.raises(DocumentError, match="already has"):
        add_document(library, extract_pdf(data, "different.pdf", DOCUMENT_TYPES[5]))
    assert len(library) == 1


def test_uploaded_packet_enters_real_graph_with_pdf_provenance(cases):
    # Two separately uploaded text PDFs: a worksheet and supporting insurance.
    primary = extract_pdf(pdf_bytes([cases["missing"].current.source_text]), "purchase.pdf", DOCUMENT_TYPES[0])
    support = extract_pdf(pdf_bytes(["Homeowners insurance: USD 165.00 per month."]), "insurance.pdf", DOCUMENT_TYPES[3], date(2026, 10, 24))
    library = {doc.document_id: doc for doc in (primary, support)}
    case = build_uploaded_case(library, primary.document_id, None, cases["clean"].saved, [support.document_id])
    assert not case.is_synthetic
    assert case.saved.name == "Synthetic saved comparison" and case.saved.source_kind == "synthetic"
    assert case.current.source_kind == "uploaded" and not case.expected_facts
    model = EvidenceModel()
    service = ReviewService(model, FakeSearch())
    report = service.snapshot(service.start(case, Limits(monthly="3300", cash="95000")))["state"]["report"]
    assert report["checks"]["current"]["monthly_total"] == "3235.00"
    assert not report["clarification_used"]
    assert report["checks"]["current"]["as_of"] == "Not provided"
    insurance = [x for x in report["checks"]["current"]["citations"].values() if x["field"] == "Homeowners insurance"]
    assert insurance and insurance[0]["source_title"] == "insurance.pdf"
    assert insurance[0]["document_type"] == DOCUMENT_TYPES[3]
    assert insurance[0]["as_of"] == "2026-10-24"
    first_payload = next(payload for role, payload in model.calls if role == "extraction")
    assert first_payload["document_type"] == DOCUMENT_TYPES[0]


def test_two_uploaded_deals_and_selection_guards(cases):
    current = extract_pdf(pdf_bytes([cases["clean"].current.source_text]), "new.pdf", DOCUMENT_TYPES[0])
    saved = extract_pdf(pdf_bytes([cases["clean"].saved.source_text]), "saved.pdf", DOCUMENT_TYPES[1])
    library = {doc.document_id: doc for doc in (current, saved)}
    case = build_uploaded_case(library, current.document_id, saved.document_id, cases["clean"].saved)
    assert case.saved.source_kind == "uploaded" and case.saved.name == "saved.pdf"
    with pytest.raises(DocumentError, match="just one role"):
        build_uploaded_case(library, current.document_id, current.document_id, cases["clean"].saved)
    with pytest.raises(DocumentError, match="still in your session"):
        build_uploaded_case(library, "missing-id", None, cases["clean"].saved)


def test_uploaded_missing_amount_resumes_with_reviewer_provenance(cases):
    doc = extract_pdf(pdf_bytes([cases["missing"].current.source_text]), "missing.pdf", DOCUMENT_TYPES[0])
    case = build_uploaded_case({doc.document_id: doc}, doc.document_id, None, cases["clean"].saved)
    service = ReviewService(EvidenceModel(), FakeSearch())
    review_id = service.start(case, Limits(monthly="3300", cash="95000"))
    assert service.snapshot(review_id)["pending"]["fields"] == ["home_insurance"]
    service.resume(review_id, {"home_insurance": "150"})
    report = service.snapshot(review_id)["state"]["report"]
    assert report["checks"]["current"]["monthly_total"] == "3220.00"
    assert report["clarification_used"]
    assert any(c["provenance"] == "reviewer" and c["source_title"] == "Reviewer confirmation" for c in report["checks"]["current"]["citations"].values())
