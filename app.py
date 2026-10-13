"""Viva Question Bot: the Streamlit user interface.

This file only handles layout and session state. Reading PDFs and search
live in rag.py; talking to the LLM lives in llm.py.
Run with:  streamlit run app.py
"""

import base64
from html import escape
from pathlib import Path

import streamlit as st

import config
from llm import LLMError, answer_question, generate_quiz, generate_viva
from prompts import NOT_FOUND
from rag import NotesIndex, PDFReadError, chunk_pages, extract_pages, format_context, get_embedder

ASSETS = Path(__file__).parent / "assets"
LOGO = ASSETS / "logo.png"
BEGIN_MESSAGE = "Upload and process your PDF notes in the sidebar to begin."

# What each tab will do, shown before any notes are uploaded.
TAB_PROMISES = {
    "viva": "You'll get viva questions with key points, model answers and marks.",
    "doubt": "Ask anything about your notes. Every answer names the page it came from.",
    "quiz": "You'll get five multiple-choice questions and a score at the end.",
}


def load_css() -> None:
    """Add our exam-paper styling (assets/style.css) on top of the Streamlit theme."""
    css = (ASSETS / "style.css").read_text(encoding="utf-8")
    st.html(f"<style>{css}</style>")


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


MARKS = {"easy": 2, "medium": 3, "hard": 5}  # the marks rule in VIVA_PROMPT


def teach(*badges: tuple[str, str]) -> None:
    """Teaching mode: show which Unit 2 idea is at work, as (label, text) badges.
    Text may contain <code> tags; everything else is plain text. Off by default."""
    if not st.session_state.get("teaching_mode"):
        return
    rows = []
    for label, text in badges:
        kind = "warn" if label.startswith(("L3 · Halluc", "Bias")) else ""
        rows.append(f'<p class="vb-teach {kind}"><b>{label}</b>{text}</p>')
    st.html(f'<div class="vb-teach-row">{"".join(rows)}</div>')


# ---------------------------------------------------------------- header / footer

def render_header() -> None:
    """College name, department and course, laid out like the top of a question paper."""
    logo = ""
    if LOGO.exists():
        data = base64.b64encode(LOGO.read_bytes()).decode()
        logo = f'<img src="data:image/png;base64,{data}" alt="">'
    st.html(
        f"""<div class="vb-masthead">{logo}<div>
        <p class="vb-college">{escape(config.COLLEGE_NAME)}</p>
        <p class="vb-dept">{escape(config.DEPARTMENT)} · {escape(config.COURSE)}</p>
        </div></div>
        <hr class="vb-rule">
        <h1 class="vb-title">Viva Question Bot</h1>
        <p class="vb-lede">Upload your PDF notes. Practise viva questions, clear doubts and quiz
        yourself — answers come only from your notes.</p>"""
    )


def render_empty_state(tab: str) -> None:
    """Friendly first screen for a tab, before any notes are processed."""
    st.html(
        f"""<div class="vb-empty"><h3>Start with your notes</h3>
        <p>{BEGIN_MESSAGE} {TAB_PROMISES[tab]}</p>
        <ol><li>Upload one or more PDF notes in the sidebar.</li>
        <li>Click <b>Process notes</b>.</li>
        <li>Come back to this tab.</li></ol></div>"""
    )


def render_footer() -> None:
    """Small grey reminder at the bottom of every screen."""
    st.html(
        '<p class="vb-footer">Answers come only from your uploaded notes. Always check the source. '
        "Nothing you upload is saved.</p>"
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
        pages = f"{info['pages']} page" + ("s" if info["pages"] != 1 else "")
        st.html(f'<div class="vb-file"><span>{escape(info["name"])}</span><span>{pages}</span></div>')
        if info["scanned"]:
            numbers = ", ".join(str(n) for n in info["scanned"])
            st.warning(
                f"Pages {numbers} have no readable text — they may be scanned images "
                "and will be skipped."
            )
    stats = st.session_state.index.stats()
    cells = "".join(f"<div><b>{stats[k]}</b><span>{k}</span></div>" for k in ("files", "pages", "chunks"))
    st.html(f'<div class="vb-stats">{cells}</div>')


def render_sidebar() -> None:
    """Upload, process and clear notes."""
    with st.sidebar:
        st.header("Your notes")
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
            teach(("RAG", "Retrieve, then generate: only the chunks that match a request go to the AI."))
        if st.button("Clear all notes", use_container_width=True):
            clear_all()
            st.rerun()
        st.divider()
        st.toggle(
            "Teaching mode",
            key="teaching_mode",
            help="Show which Unit 2 idea is at work above each output. For classroom demos.",
        )


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


def teach_viva(viva: dict) -> None:
    """Teaching-mode badges for the viva tab: the Lecture 4 lab checklist."""
    difficulty = viva["difficulty"]
    badges = [
        ("L2 · Role", "The prompt starts: <i>“You are a strict but fair viva examiner.”</i>"),
        ("L4 · Template", f"<code>{{difficulty}}</code> was filled with “{difficulty}”, so each question is worth {MARKS[difficulty]} marks."),
        ("L2 · Delimiting", "Your notes are inside <code>&lt;notes&gt;</code> tags: study material, never instructions."),
        ("L4 · Chain-of-Thought", "Key points are written first, then the model answer."),
        ("L2 · JSON", "The AI replied in JSON; the app turned it into these cards."),
    ]
    if viva.get("topic"):
        badges.insert(1, ("RAG", f"The topic “{escape(viva['topic'])}” chose which chunks were retrieved."))
    teach(*badges)


def render_viva_cards(viva: dict) -> None:
    """One bordered card per question, with answers hidden in expanders."""
    teach_viva(viva)
    if viva["note"]:
        st.html(f'<div class="vb-note">{escape(str(viva["note"]))}</div>')
    for n, q in enumerate(viva["questions"], start=1):
        # The key gives the card a CSS class (st-key-viva_card_N) that style.css styles.
        with st.container(border=True, key=f"viva_card_{n}"):
            # escape() stops text from the notes being treated as HTML.
            st.html(
                f"""<div class="vb-q"><span class="vb-qnum">Q{n}</span>
                <p class="vb-qtext">{escape(q["question"])}</p>
                <span class="vb-marks">[{q["marks"]} marks]</span></div>"""
            )
            with st.expander("Show key points", icon=":material/checklist:"):
                st.markdown("\n".join(f"- {point}" for point in q["key_points"]))
            with st.expander("Show model answer", icon=":material/menu_book:"):
                st.write(q["model_answer"])
                st.html(f'<p class="vb-source">Source: <b>{escape(q["source"])}</b></p>')
    if viva["questions"]:
        st.download_button(
            "Download as text", viva_to_text(viva), file_name="viva_questions.txt", icon=":material/download:"
        )


def render_viva_tab() -> None:
    """Main feature: generate viva questions at a chosen difficulty."""
    difficulty = st.radio("Difficulty", ["easy", "medium", "hard"], horizontal=True)
    topic = st.text_input("Topic (optional)", placeholder="Leave empty to cover the whole document", key="viva_topic")
    if st.button("Generate viva questions", type="primary", icon=":material/auto_awesome:"):
        chunks = st.session_state.index.sample_for_generation(topic, config.TOP_K_GENERATE)
        with st.spinner("Thinking…"):
            try:
                questions, note = generate_viva(format_context(chunks), difficulty)
                st.session_state.viva = {
                    "questions": questions, "note": note, "difficulty": difficulty, "topic": topic.strip()
                }
            except Exception as error:
                show_error(error)
    if st.session_state.viva:
        render_viva_cards(st.session_state.viva)


# ---------------------------------------------------------------- tab 2: ask a doubt

def render_message(message: dict) -> None:
    """Draw one chat bubble, with the retrieved chunks under assistant answers."""
    avatar = ":material/school:" if message["role"] == "assistant" else ":material/person:"
    not_found = message["role"] == "assistant" and NOT_FOUND.rstrip(".") in message["content"]
    with st.chat_message(message["role"], avatar=avatar):
        if not_found:
            teach(("L3 · Hallucination", "The prompt allows “not found”, so the bot admits it instead of guessing."))
            st.info(message["content"])  # not an error: this is the bot being honest
        else:
            if message["role"] == "assistant":
                teach(("L3 · Grounding", "Answered only from the <code>&lt;context&gt;</code>, with [file, p.X] citations. Check Sources used."))
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
        with st.container(border=True, key=f"quiz_card_{n}"):
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
                verdict = ("right", "Correct") if correct else ("wrong", "Not quite")  # icons come from style.css
                answer = escape(q["options"][q["answer_index"]])
                st.html(
                    f"""<p class="vb-verdict {verdict[0]}">{verdict[1]} · answer: {answer}</p>
                    <p class="vb-explain">{escape(q["explanation"])}<br>Source: {escape(q["source"])}</p>"""
                )
    return score


def render_score(score: int, total: int) -> None:
    """The score, circled in red ink like a marked answer sheet."""
    remarks = {total: "Full marks!", 0: "Read the notes again and try a new quiz."}
    remark = remarks.get(score, "Check the explanations above for the ones you missed.")
    st.html(
        f"""<div class="vb-score"><span class="vb-score-ring">{score}/{total}</span>
        <span class="vb-score-label">Your score<small>{remark}</small></span></div>"""
    )


def render_quiz_tab() -> None:
    """Make a 5-question multiple-choice quiz and score it."""
    if st.session_state.quiz is None:
        topic = st.text_input("Topic (optional)", placeholder="Leave empty to cover the whole document", key="quiz_topic")
        if st.button("Make a quiz", type="primary", icon=":material/quiz:"):
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
    teach(
        ("L1 · In-context learning", "The AI copied the JSON format example shown in the prompt."),
        ("Bias fix", "Options are shuffled in code, so the right answer isn't always A (L1: examples can bias answers)."),
    )
    score = render_quiz_questions(quiz)
    if st.session_state.quiz_checked:
        teach(("L3 · Accuracy", f"Correct ÷ total × 100 = {score} ÷ {len(quiz)} × 100 = {round(100 * score / len(quiz))}%"))
        render_score(score, len(quiz))
    with st.container(horizontal=True):
        if not st.session_state.quiz_checked:
            if st.button("Check answers", type="primary", icon=":material/done_all:"):
                st.session_state.quiz_checked = True
                st.rerun()
        if st.button("New quiz", icon=":material/refresh:"):
            new_quiz()
            st.rerun()


# ---------------------------------------------------------------- main

def main() -> None:
    """Build the whole page."""
    st.set_page_config(page_title="Viva Question Bot", page_icon="🎓", layout="wide")
    load_css()
    init_state()
    render_header()
    render_sidebar()
    tabs = st.tabs(
        [":material/record_voice_over: Viva questions", ":material/forum: Ask a doubt", ":material/quiz: Quiz me"]
    )
    renderers = {"viva": render_viva_tab, "doubt": render_doubt_tab, "quiz": render_quiz_tab}
    for tab, (name, render) in zip(tabs, renderers.items()):
        with tab:
            if notes_ready():
                render()
            else:
                render_empty_state(name)
    render_footer()


main()
