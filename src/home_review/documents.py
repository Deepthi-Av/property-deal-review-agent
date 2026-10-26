"""Bounded PDF text import and session-only review packet assembly."""

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from uuid import uuid4

from pypdf import PdfReader

from home_review.models import Deal, DemoCase, SourceDocument

DOCUMENT_TYPES = (
    "Purchase cost worksheet",
    "Loan estimate",
    "Closing disclosure",
    "Insurance quote or correspondence",
    "HOA statement or correspondence",
    "Property tax statement",
    "Other home-purchase document",
)
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 12
MAX_TEXT_CHARS = 12000
MAX_DOCUMENTS = 8


class DocumentError(ValueError):
    """A safe, actionable import or selection error."""


@dataclass(frozen=True, repr=False)
class UploadedDocument:
    document_id: str
    filename: str
    document_type: str
    as_of: date | None
    text: str
    page_count: int
    pdf_bytes: bytes = field(repr=False)

    @property
    def label(self) -> str:
        # The suffix distinguishes revisions uploaded with the same filename.
        return f"{self.filename} · {self.document_type} · {self.document_id[-6:]}"


def extract_pdf(data: bytes, filename: str, document_type: str,
                as_of: date | None = None) -> UploadedDocument:
    """Extract every page or reject the file; never silently truncate a packet.

    This reads selectable text only. It does not OCR scans, execute PDF actions,
    write uploaded data to disk, or call an external service.
    """
    filename = PurePosixPath(filename.replace("\\", "/")).name
    if not filename.lower().endswith(".pdf") or not data.startswith(b"%PDF-"):
        raise DocumentError("Choose a valid PDF file. Renaming another file to .pdf is not supported.")
    if len(data) > MAX_PDF_BYTES:
        raise DocumentError("This PDF exceeds 10 MB. Upload a smaller document.")
    if document_type not in DOCUMENT_TYPES:
        raise DocumentError("Choose a document type before adding the PDF.")
    if len(filename) > 160:
        raise DocumentError("Use a filename of 160 characters or fewer.")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted:
            raise DocumentError("Password-protected PDFs are not supported. Upload an unlocked copy.")
        if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise DocumentError(f"Upload a PDF with 1–{MAX_PDF_PAGES} pages.")
        # Form-field values may differ from their visible appearance. Do not
        # silently interpret one representation while ignoring the other.
        if reader.get_fields():
            raise DocumentError("This PDF contains interactive form fields. Export a flattened, text-based copy before uploading.")
        pages = []
        total = 0
        for index, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").replace("\x00", "").strip()
            if not text:
                raise DocumentError(f"Page {index} has no readable text. Remove blank pages or upload a text-based PDF; scanned pages need OCR first.")
            if "\ufffd" in text:
                raise DocumentError(f"Page {index} contains unreadable characters. Export a fresh text-based PDF.")
            page_text = f"[Page {index}]\n{text}"
            total += len(page_text) + (2 if pages else 0)
            if total > MAX_TEXT_CHARS:
                raise DocumentError(f"The extracted text exceeds {MAX_TEXT_CHARS:,} characters. Upload a shorter document; no pages were silently discarded.")
            pages.append(page_text)
        extracted = "\n\n".join(pages)
    except DocumentError:
        raise
    except Exception:
        raise DocumentError("This PDF could not be read. It may be damaged or use unsupported encoding. Export a fresh text-based copy.") from None
    identity = sha256(data + filename.encode() + document_type.encode() + str(as_of).encode()).hexdigest()[:20]
    return UploadedDocument("pdf-" + identity, filename, document_type, as_of,
                            extracted, len(pages), data)


def add_document(library: dict[str, UploadedDocument], document: UploadedDocument) -> bool:
    """Return False for an exact duplicate without consuming another library slot."""
    if document.document_id in library:
        return False
    if len(library) >= MAX_DOCUMENTS:
        raise DocumentError(f"This session already has {MAX_DOCUMENTS} PDFs. Remove one before adding another.")
    library[document.document_id] = document
    return True


def build_uploaded_case(library: dict[str, UploadedDocument], current_id: str,
                        saved_id: str | None, synthetic_saved: Deal,
                        current_support: list[str] | None = None,
                        saved_support: list[str] | None = None) -> DemoCase:
    current_support, saved_support = current_support or [], saved_support or []
    all_ids = [current_id, *current_support, *([saved_id] if saved_id else []), *saved_support]
    if any(key not in library for key in all_ids):
        raise DocumentError("Choose documents that are still in your session library.")
    if len(set(all_ids)) != len(all_ids):
        raise DocumentError("Assign each PDF to just one role and one deal.")
    if len(current_support) > 2 or len(saved_support) > 2:
        raise DocumentError("Choose at most two supporting PDFs per deal.")
    if saved_support and not saved_id:
        raise DocumentError("Choose an uploaded comparison document before adding its supporting PDFs.")

    def deal(document_id: str, supporting: list[str]) -> Deal:
        doc = library[document_id]
        return Deal(deal_id=document_id, name=doc.filename, as_of=doc.as_of,
                    source_text=doc.text, source_kind="uploaded", document_type=doc.document_type,
                    documents=[SourceDocument(source_id=key, title=library[key].filename,
                        as_of=library[key].as_of, text=library[key].text,
                        document_type=library[key].document_type) for key in supporting])

    saved = deal(saved_id, saved_support) if saved_id else synthetic_saved.model_copy(deep=True)
    if not saved_id:
        saved.name = "Synthetic saved comparison"
    return DemoCase(case_id="upload-" + uuid4().hex, title="PDF review: " + library[current_id].filename,
                    description="Uploaded PDFs with a synthetic saved comparison." if not saved_id else "Comparison of uploaded PDF documents.",
                    is_synthetic=False, current=deal(current_id, current_support), saved=saved)
