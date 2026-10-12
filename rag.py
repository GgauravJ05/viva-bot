"""The RAG pipeline: read PDFs, cut them into chunks, embed them, and search them.

RAG = Retrieval-Augmented Generation. Before asking the LLM anything, we
*retrieve* the parts of the notes that matter, and give only those to the LLM.
Everything here stays in memory: nothing is written to disk.
"""

import io
import re
import uuid
from dataclasses import dataclass

import chromadb
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer

from config import CHUNK_OVERLAP, CHUNK_SIZE, EMBED_MODEL, MIN_CHARS_PER_PAGE


class PDFReadError(Exception):
    """Raised when a PDF cannot be read. The message is safe to show to users."""


@dataclass
class Page:
    file_name: str
    page_number: int  # 1-based, as a human reads it
    text: str


@dataclass
class Chunk:
    chunk_id: str  # f"{file_name}-p{page_number}-{index}"
    file_name: str
    page_number: int
    text: str


def extract_pages(file_bytes: bytes, file_name: str) -> tuple[list[Page], list[int]]:
    """Read a PDF and return (pages with text, page numbers that look scanned/empty)."""
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        if reader.is_encrypted:
            raise PDFReadError(f"{file_name} is password-protected. Upload an unlocked copy.")
        pages, scanned = [], []
        for number, pdf_page in enumerate(reader.pages, start=1):
            # Collapse runs of spaces/newlines so chunk sizes reflect real content.
            text = re.sub(r"\s+", " ", pdf_page.extract_text() or "").strip()
            if len(text) < MIN_CHARS_PER_PAGE:
                scanned.append(number)
            else:
                pages.append(Page(file_name, number, text))
    except PDFReadError:
        raise
    except Exception as error:  # pypdf raises many different error types
        raise PDFReadError(f"Could not read {file_name}. Is it a valid PDF?") from error
    return pages, scanned


def _find_break(text: str, start: int, size: int) -> int:
    """Pick where a chunk starting at `start` should end, preferring a sentence end."""
    end = start + size
    if end >= len(text):
        return len(text)
    # Look for the last sentence end in the second half of the window,
    # so we never produce a tiny chunk.
    window = text[start + size // 2 : end]
    cut = max(window.rfind(". "), window.rfind("\n"))
    if cut == -1:
        return end  # no good place: cut exactly at the size limit
    return start + size // 2 + cut + 1  # keep the full stop in this chunk


def chunk_pages(pages: list[Page], size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[Chunk]:
    """Split each page into overlapping chunks. Chunks never cross pages,
    so every chunk has exactly one page number to cite."""
    chunks = []
    for page in pages:
        text, start, index = page.text, 0, 0
        while start < len(text):
            end = _find_break(text, start, size)
            piece = text[start:end].strip()
            if piece:
                chunk_id = f"{page.file_name}-p{page.page_number}-{index}"
                chunks.append(Chunk(chunk_id, page.file_name, page.page_number, piece))
                index += 1
            if end >= len(text):
                break
            # Step back by `overlap` characters so boundary sentences appear twice.
            start = max(end - overlap, start + 1)
    return chunks


def format_context(chunks: list[Chunk]) -> str:
    """Turn chunks into the text we place inside the <context>/<notes> tags."""
    blocks = [f"[Source: {c.file_name}, p.{c.page_number}]\n{c.text}" for c in chunks]
    return "\n\n".join(blocks)


_embedder: SentenceTransformer | None = None


def get_embedder() -> SentenceTransformer:
    """Load the embedding model once and reuse it (loading takes a few seconds)."""
    global _embedder
    if _embedder is None:
        try:
            # Use the copy already downloaded, so slow classroom Wi-Fi can't stall startup.
            _embedder = SentenceTransformer(EMBED_MODEL, local_files_only=True)
        except Exception:
            _embedder = SentenceTransformer(EMBED_MODEL)  # first run: download it (~90 MB)
    return _embedder


class NotesIndex:
    """An in-memory search index over the uploaded notes."""

    def __init__(self, embedder: SentenceTransformer | None = None) -> None:
        """Create an empty index. Pass a cached embedder to avoid reloading it."""
        self.embedder = embedder or get_embedder()
        self.client = chromadb.EphemeralClient()  # in memory only, nothing on disk
        self._new_collection()

    def _new_collection(self) -> None:
        """Start a fresh, empty collection with a unique name for this session."""
        self.name = f"notes-{uuid.uuid4().hex[:8]}"
        # Cosine distance suits sentence-transformer embeddings.
        self.collection = self.client.create_collection(self.name, metadata={"hnsw:space": "cosine"})
        self.chunks: list[Chunk] = []

    def add(self, chunks: list[Chunk], batch_size: int = 64) -> None:
        """Embed the chunks and store them with their file name and page number."""
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            vectors = self.embedder.encode([c.text for c in batch]).tolist()
            self.collection.add(
                ids=[c.chunk_id for c in batch],
                embeddings=vectors,
                documents=[c.text for c in batch],
                metadatas=[{"file_name": c.file_name, "page_number": c.page_number} for c in batch],
            )
        self.chunks.extend(chunks)

    def search(self, query: str, k: int) -> list[Chunk]:
        """Return the k chunks most similar in meaning to the query, best first."""
        if not self.chunks:
            return []
        vector = self.embedder.encode([query]).tolist()
        result = self.collection.query(query_embeddings=vector, n_results=min(k, len(self.chunks)))
        found = zip(result["ids"][0], result["documents"][0], result["metadatas"][0])
        return [Chunk(cid, meta["file_name"], int(meta["page_number"]), doc) for cid, doc, meta in found]

    def sample_for_generation(self, topic: str | None, k: int) -> list[Chunk]:
        """Chunks for viva/quiz: about the topic if given, else spread over the whole notes."""
        if topic and topic.strip():
            return self.search(topic, k)
        if len(self.chunks) <= k:
            return list(self.chunks)
        # Pick evenly spaced chunks so questions cover the whole document, not just the start.
        step = len(self.chunks) / k
        return [self.chunks[int(i * step)] for i in range(k)]

    def clear(self) -> None:
        """Delete everything and start empty."""
        self.client.delete_collection(self.name)
        self._new_collection()

    def stats(self) -> dict:
        """Count files, pages (with text) and chunks in the index."""
        files = {c.file_name for c in self.chunks}
        pages = {(c.file_name, c.page_number) for c in self.chunks}
        return {"files": len(files), "pages": len(pages), "chunks": len(self.chunks)}
