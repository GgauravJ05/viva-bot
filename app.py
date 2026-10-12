"""Viva Question Bot: the Streamlit user interface.

This file only handles layout and session state. Reading PDFs and search
live in rag.py; talking to the LLM lives in llm.py.
Run with:  streamlit run app.py
"""

from pathlib import Path

import streamlit as st

import config
from llm import LLMError, answer_question, generate_quiz, generate_viva
from prompts import NOT_FOUND
from rag import NotesIndex, PDFReadError, chunk_pages, extract_pages, format_context, get_embedder

LOGO = Path(__file__).parent / "assets" / "logo.png"
BEGIN_MESSAGE = "Upload and process your PDF notes in the sidebar to begin."


@st.cache_resource(show_spinner="Loading the search model (first run downloads ~90 MB)…")
def load_embedder():
    """Load the embedding model once for the whole app, not on every click."""
    return get_embedder()


def init_state() -> None:
    """Create the session variables the first time the page loads."""
    defaults = {
        "index": None,        # NotesIndex, kept so we never re-embed on a rerun
        "files_info": [],     # [{"name", "pages", "scanned"}] for the sidebar list
        "viva": None,         # {"questions", "note", "difficulty"}
        "messages": [],       # Ask-a-doubt chat history for this session
        "quiz": None,         # list of quiz questions
        "quiz_checked": False,
        "quiz_round": 0,      # changes the radio keys so a new quiz starts blank
        "uploader_round": 0,  # changes the uploader key so "Clear" empties it
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def notes_ready() -> bool:
    """True once notes have been processed into a non-empty index."""
    index = st.session_state.index
    return index is not None and index.stats()["chunks"] > 0


def show_error(error: Exception) -> None:
    """Show a friendly error. Students never see a Python traceback."""
    if isinstance(error, (LLMError, PDFReadError)):
        st.error(str(error))
    else:
        st.error("Something went wrong. Please try again.")


# ---------------------------------------------------------------- header / footer

def render_header() -> None:
    """College name, department, course and the app title."""
    if LOGO.exists():
        logo_col, text_col = st.columns([1, 8])
        logo_col.image(str(LOGO), width=90)
    else:
        text_col = st.container()
    with text_col:
        st.markdown(f"### **{config.COLLEGE_NAME}**")
        st.caption(f"{config.DEPARTMENT} · {config.COURSE}")
    st.title("🎓 Viva Question Bot")
    st.write(
        "Upload your PDF notes. Practise viva questions, clear doubts and quiz yourself "
        "— answers come only from your notes."
    )


def render_footer() -> None:
    """Small grey reminder at the bottom of every screen."""
    st.divider()
    st.caption(
        "Answers come only from your uploaded notes. Always check the source. "
        "Nothing you upload is saved."
    )


# ---------------------------------------------------------------- sidebar

def process_notes(uploads: list) -> None:
    """Extract, chunk and index the uploaded PDFs into a fresh index."""
    if len(uploads) > config.MAX_FILES:
        st.error(f"You can upload a maximum of {config.MAX_FILES} files at a time.")
        return
    all_pages, files_info, total_pages = [], [], 0
    for upload in uploads:
        pages, scanned = extract_pages(upload.getvalue(), upload.name)
        total_pages += len(pages) + len(scanned)
        all_pages.extend(pages)
        files_info.append({"name": upload.name, "pages": len(pages) + len(scanned), "scanned": scanned})
    if total_pages > config.MAX_PAGES_TOTAL:
        st.error(f"That is {total_pages} pages. The demo limit is {config.MAX_PAGES_TOTAL} pages in total.")
        return
    if not all_pages:
        st.error("No readable text found. These PDFs may be scanned images.")
        return
    index = NotesIndex(load_embedder())
    index.add(chunk_pages(all_pages))
    reset_outputs()
    st.session_state.index = index
    st.session_state.files_info = files_info


def reset_outputs() -> None:
    """Forget generated questions, chat and quiz (they belong to the old notes)."""
    st.session_state.viva = None
    st.session_state.messages = []
    st.session_state.quiz = None
    st.session_state.quiz_checked = False


def clear_all() -> None:
    """Empty the index, the uploader and every tab."""
    if st.session_state.index is not None:
        st.session_state.index.clear()
    st.session_state.index = None
    st.session_state.files_info = []
    st.session_state.uploader_round += 1
    reset_outputs()


def render_file_list() -> None:
    """Show each processed file, scanned-page warnings and overall stats."""
    for info in st.session_state.files_info:
        st.markdown(f"📄 **{info['name']}** · {info['pages']} pages")
        if info["scanned"]:
            numbers = ", ".join(str(n) for n in info["scanned"])
            st.warning(
                f"Pages {numbers} have no readable text — they may be scanned images "
                "and will be skipped."
            )
    stats = st.session_state.index.stats()
    files_col, pages_col, chunks_col = st.columns(3)
    files_col.metric("Files", stats["files"])
    pages_col.metric("Pages", stats["pages"])
    chunks_col.metric("Chunks", stats["chunks"])


def render_sidebar() -> None:
    """Upload, process and clear notes."""
    with st.sidebar:
        st.header("📚 Your notes")
        uploads = st.file_uploader(
            "Upload PDF notes",
            type=["pdf"],
            accept_multiple_files=True,
            key=f"uploader_{st.session_state.uploader_round}",
        )
        if st.button("Process notes", type="primary", disabled=not uploads, use_container_width=True):
            with st.spinner("Reading your notes…"):
                try:
                    process_notes(uploads)
                except Exception as error:
                    show_error(error)
        if notes_ready():
            render_file_list()
        if st.button("Clear all notes", use_container_width=True):
            clear_all()
            st.rerun()


# ---------------------------------------------------------------- tab 1: viva

def viva_to_text(viva: dict) -> str:
    """Plain-text version of the viva questions for the download button."""
    lines = [f"Viva questions ({viva['difficulty']})", ""]
    for n, q in enumerate(viva["questions"], start=1):
        lines.append(f"Q{n} ({q['marks']} marks): {q['question']}")
        lines.append("Key points:")
        lines.extend(f"  - {point}" for point in q["key_points"])
        lines.append(f"Model answer: {q['model_answer']}")
        lines.append(f"Source: {q['source']}")
        lines.append("")
    return "\n".join(lines)


def render_viva_cards(viva: dict) -> None:
    """One bordered card per question, with answers hidden in expanders."""
    if viva["note"]:
        st.info(viva["note"])
    for n, q in enumerate(viva["questions"], start=1):
        with st.container(border=True):
            st.caption(f"Q{n} · {q['marks']} marks")
            st.markdown(f"**{q['question']}**")
            with st.expander("Show key points"):
                st.markdown("\n".join(f"- {point}" for point in q["key_points"]))
            with st.expander("Show model answer"):
                st.write(q["model_answer"])
                st.caption(f"Source: {q['source']}")
    if viva["questions"]:
        st.download_button("Download as text", viva_to_text(viva), file_name="viva_questions.txt")


def render_viva_tab() -> None:
    """Main feature: generate viva questions at a chosen difficulty."""
    difficulty = st.radio("Difficulty", ["easy", "medium", "hard"], horizontal=True)
    topic = st.text_input("Topic (optional)", placeholder="Leave empty to cover the whole document", key="viva_topic")
    if st.button("Generate viva questions", type="primary"):
        chunks = st.session_state.index.sample_for_generation(topic, config.TOP_K_GENERATE)
        with st.spinner("Thinking…"):
            try:
                questions, note = generate_viva(format_context(chunks), difficulty)
                st.session_state.viva = {"questions": questions, "note": note, "difficulty": difficulty}
            except Exception as error:
                show_error(error)
    if st.session_state.viva:
        render_viva_cards(st.session_state.viva)


# ---------------------------------------------------------------- tab 2: ask a doubt

def render_message(message: dict) -> None:
    """Draw one chat bubble, with the retrieved chunks under assistant answers."""
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and NOT_FOUND.rstrip(".") in message["content"]:
            st.info(message["content"])  # not an error: this is the bot being honest
        else:
            st.markdown(message["content"])
        if message.get("sources"):
            with st.expander("Sources used"):
                for chunk in message["sources"]:
                    st.markdown(f"**{chunk.file_name}, p.{chunk.page_number}**")
                    st.caption(chunk.text)


def render_doubt_tab() -> None:
    """Chat that answers only from the notes, with citations."""
    for message in st.session_state.messages:
        render_message(message)
    question = st.chat_input("Ask a doubt about your notes…")
    if not question:
        return
    user_message = {"role": "user", "content": question}
    st.session_state.messages.append(user_message)
    render_message(user_message)
    chunks = st.session_state.index.search(question, config.TOP_K_QA)
    with st.spinner("Thinking…"):
        try:
            answer = answer_question(format_context(chunks), question)
        except Exception as error:
            show_error(error)
            return
    reply = {"role": "assistant", "content": answer, "sources": chunks}
    st.session_state.messages.append(reply)
    render_message(reply)


# ---------------------------------------------------------------- tab 3: quiz

def new_quiz() -> None:
    """Forget the current quiz and start the radios fresh next time."""
    st.session_state.quiz = None
    st.session_state.quiz_checked = False
    st.session_state.quiz_round += 1


def render_quiz_questions(quiz: list[dict]) -> int:
    """Show each question as radio buttons; after checking, mark it. Returns the score."""
    score = 0
    for n, q in enumerate(quiz, start=1):
        with st.container(border=True):
            choice = st.radio(
                f"**Q{n}. {q['question']}**",
                range(4),
                format_func=lambda i, q=q: q["options"][i],
                index=None,  # no answer picked by default
                key=f"quiz_{st.session_state.quiz_round}_{n}",
                disabled=st.session_state.quiz_checked,
            )
            if st.session_state.quiz_checked:
                correct = choice == q["answer_index"]
                score += correct
                mark = "✅ Correct" if correct else "❌ Wrong"
                st.markdown(f"{mark} — answer: **{q['options'][q['answer_index']]}**")
                st.caption(f"{q['explanation']}  \nSource: {q['source']}")
    return score


def render_quiz_tab() -> None:
    """Make a 5-question multiple-choice quiz and score it."""
    if st.session_state.quiz is None:
        topic = st.text_input("Topic (optional)", placeholder="Leave empty to cover the whole document", key="quiz_topic")
        if st.button("Make a quiz", type="primary"):
            chunks = st.session_state.index.sample_for_generation(topic, config.TOP_K_GENERATE)
            with st.spinner("Thinking…"):
                try:
                    quiz = generate_quiz(format_context(chunks))
                except Exception as error:
                    show_error(error)
                    return
            st.session_state.quiz = quiz
            st.rerun()  # redraw so the quiz replaces the topic box
        return
    quiz = st.session_state.quiz
    score = render_quiz_questions(quiz)
    check_col, new_col = st.columns([1, 1])
    if not st.session_state.quiz_checked:
        if check_col.button("Check answers", type="primary"):
            st.session_state.quiz_checked = True
            st.rerun()
    else:
        st.subheader(f"Score: {score} / {len(quiz)}")
    if new_col.button("New quiz"):
        new_quiz()
        st.rerun()


# ---------------------------------------------------------------- main

def main() -> None:
    """Build the whole page."""
    st.set_page_config(page_title="Viva Question Bot", page_icon="🎓", layout="wide")
    init_state()
    render_header()
    render_sidebar()
    viva_tab, doubt_tab, quiz_tab = st.tabs(["🎤 Viva questions", "💬 Ask a doubt", "📝 Quiz me"])
    for tab, render in [(viva_tab, render_viva_tab), (doubt_tab, render_doubt_tab), (quiz_tab, render_quiz_tab)]:
        with tab:
            if notes_ready():
                render()
            else:
                st.info(BEGIN_MESSAGE)
    render_footer()


main()
