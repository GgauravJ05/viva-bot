"""Tests for the RAG pipeline. Run with:  pytest"""

import io
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

# Let the tests import modules from the project folder.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rag import NotesIndex, Page, PDFReadError, chunk_pages, extract_pages, format_context  # noqa: E402

DBMS_PDF = ROOT / "demo_notes" / "DBMS_Normalisation.pdf"


def test_extract_returns_correct_page_numbers():
    pages, scanned = extract_pages(DBMS_PDF.read_bytes(), DBMS_PDF.name)
    assert [p.page_number for p in pages] == [1, 2]
    assert "First Normal Form" in pages[0].text
    assert "Boyce-Codd" in pages[1].text
    assert scanned == []


def test_blank_page_is_flagged_as_scanned():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    pages, scanned = extract_pages(buffer.getvalue(), "blank.pdf")
    assert pages == []
    assert scanned == [1]


def test_garbage_bytes_raise_friendly_error():
    with pytest.raises(PDFReadError):
        extract_pages(b"this is not a pdf", "fake.pdf")


def test_chunks_never_cross_pages():
    pages = [Page("a.pdf", 1, "One. " * 300), Page("a.pdf", 2, "Two. " * 300)]
    chunks = chunk_pages(pages, size=200, overlap=40)
    for chunk in chunks:
        word = "One" if chunk.page_number == 1 else "Two"
        assert set(chunk.text.replace(".", "").split()) == {word}
        assert len(chunk.text) <= 200


def test_chunk_overlap_repeats_boundary_text():
    text = "".join(f"Sentence number {i:03d} is here. " for i in range(60))
    chunks = chunk_pages([Page("a.pdf", 1, text)], size=300, overlap=60)
    assert len(chunks) > 1
    for first, second in zip(chunks, chunks[1:]):
        # The start of each chunk should already appear at the end of the previous one.
        assert second.text[:20] in first.text


def test_chunk_ids_are_unique_and_readable():
    chunks = chunk_pages([Page("n.pdf", 3, "Hello world. " * 200)], size=300, overlap=50)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert ids[0] == "n.pdf-p3-0"


def test_format_context_includes_source_labels():
    chunks = chunk_pages([Page("DBMS_notes.pdf", 3, "Keys identify rows.")])
    assert format_context(chunks) == "[Source: DBMS_notes.pdf, p.3]\nKeys identify rows."


@pytest.fixture(scope="module")
def dbms_index():
    pages, _ = extract_pages(DBMS_PDF.read_bytes(), DBMS_PDF.name)
    index = NotesIndex()
    index.add(chunk_pages(pages))
    return index


def test_search_finds_bcnf_on_page_2(dbms_index):
    results = dbms_index.search("What is BCNF?", k=2)
    assert results[0].page_number == 2


def test_stats_and_clear(dbms_index):
    assert dbms_index.stats() == {"files": 1, "pages": 2, "chunks": len(dbms_index.chunks)}
    assert dbms_index.stats()["chunks"] >= 2
    assert len(dbms_index.sample_for_generation(None, k=6)) == dbms_index.stats()["chunks"]
    dbms_index.clear()
    assert dbms_index.stats() == {"files": 0, "pages": 0, "chunks": 0}
    assert dbms_index.search("BCNF", k=2) == []
