import pytest
from streamlit.testing.v1 import AppTest

from home_review.fixtures import ROOT
from home_review.workflow import ReviewService
from conftest import FakeSearch


def test_streamlit_review_clarification_and_rerun_do_not_repeat_calls(fake_model):
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=30)
    app.session_state["review_service"] = ReviewService(fake_model, FakeSearch())
    app.run()
    assert not app.exception
    assert app.selectbox(key="review_source").value == "Synthetic examples"
    assert not any("Connections" in item.value for item in app.markdown)
    app.selectbox(key="synthetic_case_id").select("missing")
    next(button for button in app.button if button.label == "Review deals").click()
    app.run()
    assert not app.exception
    assert app.text_input
    initial_calls = len(fake_model.calls)
    app.run()
    assert len(fake_model.calls) == initial_calls
    app.text_input[0].set_value("150")
    next(button for button in app.button if button.label.startswith("Continue review")).click()
    app.run()
    assert not app.exception
    service = app.session_state["review_service"]
    report = service.snapshot(app.session_state["review_id"])["state"]["report"]
    assert report["checks"]["current"]["monthly_total"] == "3220.00"
    assert report["clarification_used"] is True
    final_calls = len(fake_model.calls)
    app.run()
    assert len(fake_model.calls) == final_calls


def test_contextual_handoff_shows_evidence_and_preserves_unknown():
    from test_investigation import EvidenceModel
    model = EvidenceModel(finalize_early=True)
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=30)
    app.session_state["review_service"] = ReviewService(model, FakeSearch())
    app.run()
    app.selectbox(key="synthetic_case_id").select("evidence_placeholder")
    next(button for button in app.button if button.label == "Review deals").click()
    app.run()
    assert not app.exception
    assert any("not a broker-supplied price" in element.value for element in app.markdown)
    assert len(app.text_input) == 1
    next(button for button in app.button if button.label.startswith("Continue review")).click()
    app.run()
    assert not app.exception
    report = app.session_state["review_service"].snapshot(app.session_state["review_id"])["state"]["report"]
    assert report["checks"]["current"]["monthly_total"] is None
    assert report["clarification_used"]
    calls = len(model.calls)
    app.run()
    assert len(model.calls) == calls


def test_pdf_library_selection_review_and_reset(cases):
    from test_documents import pdf_bytes
    from test_investigation import EvidenceModel
    from home_review.documents import DOCUMENT_TYPES, extract_pdf

    doc = extract_pdf(pdf_bytes([cases["missing"].current.source_text]), "uploaded.pdf", DOCUMENT_TYPES[0])
    model = EvidenceModel()
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=30)
    app.session_state["review_service"] = ReviewService(model, FakeSearch())
    app.session_state["uploaded_documents"] = {doc.document_id: doc}
    app.run()
    app.selectbox(key="review_source").select("Uploaded PDFs").run()
    assert not app.exception
    assert app.selectbox(key="current_pdf_id").value == doc.document_id
    assert app.selectbox(key="saved_pdf_id").value is None
    assert any(item.label == "Document type" for item in app.selectbox)
    assert not model.calls  # Selection and preview do not send document text.
    next(button for button in app.button if button.label == "Review deals").click().run()
    assert not model.calls
    assert any("confirm the figures are in USD" in error.value for error in app.error)
    app.checkbox[0].check()
    next(button for button in app.button if button.label == "Review deals").click().run()
    assert not app.exception and app.text_input, [error.value for error in app.error]
    app.text_input[0].set_value("150")
    next(button for button in app.button if button.label.startswith("Continue review")).click().run()
    assert not app.exception
    service = app.session_state["review_service"]
    report = service.snapshot(app.session_state["review_id"])["state"]["report"]
    assert report["checks"]["current"]["monthly_total"] == "3220.00"
    count = len(model.calls)
    app.selectbox(key="review_source").select("Synthetic examples").run()
    assert not app.exception and len(model.calls) == count
    assert any(item.value == "PDF review: uploaded.pdf" for item in app.subheader)
    next(button for button in app.button if button.label == "Reset review").click().run()
    assert not app.exception
    assert doc.document_id in app.session_state["uploaded_documents"]
    with pytest.raises(KeyError):
        app.session_state["review_id"]


def test_pdf_mode_without_documents_disables_review():
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=30).run()
    app.selectbox(key="review_source").select("Uploaded PDFs").run()
    assert not app.exception
    assert next(button for button in app.button if button.label == "Review deals").disabled

