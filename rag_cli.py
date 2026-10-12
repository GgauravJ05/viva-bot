"""Tiny command-line test of the whole RAG pipeline, without the web UI.

Usage:  python rag_cli.py demo_notes/DBMS_Normalisation.pdf "What is 2NF?"
"""

import sys
from pathlib import Path

from config import TOP_K_QA
from llm import LLMError, answer_question
from rag import NotesIndex, PDFReadError, chunk_pages, extract_pages, format_context


def main() -> None:
    """Index one PDF, retrieve chunks for the question, and print the answer."""
    if len(sys.argv) != 3:
        print('Usage: python rag_cli.py <file.pdf> "<question>"')
        sys.exit(1)
    path, question = Path(sys.argv[1]), sys.argv[2]
    try:
        pages, scanned = extract_pages(path.read_bytes(), path.name)
        if scanned:
            print(f"Skipping pages with no text: {scanned}")
        index = NotesIndex()
        index.add(chunk_pages(pages))
        chunks = index.search(question, TOP_K_QA)
        print("Retrieved:", ", ".join(f"{c.file_name} p.{c.page_number}" for c in chunks))
        print("\n" + answer_question(format_context(chunks), question))
    except (PDFReadError, LLMError, FileNotFoundError) as error:
        print(f"Error: {error}")
        sys.exit(1)


if __name__ == "__main__":
    main()
