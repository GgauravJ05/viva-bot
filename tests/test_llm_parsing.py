"""Tests for JSON parsing and validation. No network calls: the LLM is faked."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm  # noqa: E402
from llm import LLMError, parse_json_reply, validate_quiz, validate_viva  # noqa: E402
from prompts import QA_PROMPT, QUIZ_PROMPT, VIVA_PROMPT  # noqa: E402

VIVA_ITEM = {
    "question": "What is a stack?",
    "key_points": ["Linear structure", "LIFO"],
    "model_answer": "A linear data structure that follows LIFO order.",
    "marks": 2,
    "source": "DS_Stacks.pdf, p.1",
}
QUIZ_ITEM = {
    "question": "Stacks follow which order?",
    "options": ["FIFO", "LIFO", "Random", "Sorted"],
    "answer_index": 1,
    "explanation": "A stack is Last In, First Out.",
    "source": "DS_Stacks.pdf, p.1",
}


# --- Prompts ---

def test_prompts_format_without_errors():
    qa = QA_PROMPT.format(college="MES", context="[Source: a.pdf, p.1]\nText", question="Why?")
    viva = VIVA_PROMPT.format(difficulty="hard", context="notes")
    quiz = QUIZ_PROMPT.format(context="notes")
    assert "Question: Why?" in qa and "MES" in qa
    assert "hard difficulty" in viva
    # Doubled braces must come out as single braces in the JSON example.
    assert '{"question"' in viva and '{"question"' in quiz
    assert "{{" not in viva + quiz


# --- Fence stripping and parsing ---

def test_parse_plain_json():
    assert parse_json_reply('[{"a": 1}]') == [{"a": 1}]


def test_parse_strips_json_fence():
    assert parse_json_reply('```json\n[{"a": 1}]\n```') == [{"a": 1}]


def test_parse_strips_bare_fence_and_surrounding_text():
    assert parse_json_reply('Here you go:\n```\n{"a": 1}\n```\nGood luck!') == {"a": 1}


def test_parse_rejects_invalid_json():
    with pytest.raises(ValueError):
        parse_json_reply("Sure! Question 1: what is a stack?")


# --- chat_json retry behaviour ---

def test_chat_json_retries_once_then_succeeds(monkeypatch):
    replies = iter(["not json", '[{"a": 1}]'])
    prompts = []
    monkeypatch.setattr(llm, "chat", lambda p: prompts.append(p) or next(replies))
    assert llm.chat_json("PROMPT") == [{"a": 1}]
    assert prompts[1].endswith("Reply again with ONLY valid JSON.")


def test_chat_json_gives_up_after_second_failure(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda p: "still not json")
    with pytest.raises(LLMError, match="expected format"):
        llm.chat_json("PROMPT")


def test_missing_key_gives_friendly_error(monkeypatch):
    monkeypatch.setattr(llm.config, "API_KEY", "")
    with pytest.raises(LLMError, match="Check your .env file"):
        llm.chat("hello")


# --- Viva validation ---

def test_viva_valid_list():
    questions, note = validate_viva([VIVA_ITEM, VIVA_ITEM])
    assert len(questions) == 2 and note is None


def test_viva_missing_key_is_rejected():
    broken = {k: v for k, v in VIVA_ITEM.items() if k != "model_answer"}
    with pytest.raises(LLMError):
        validate_viva([broken])


def test_viva_key_points_must_be_a_list():
    with pytest.raises(LLMError):
        validate_viva([{**VIVA_ITEM, "key_points": "LIFO"}])


def test_viva_note_inside_list():
    questions, note = validate_viva([VIVA_ITEM, {"note": "Notes too short for 3."}])
    assert len(questions) == 1 and note == "Notes too short for 3."


def test_viva_note_as_object():
    questions, note = validate_viva({"note": "Short notes.", "questions": [VIVA_ITEM]})
    assert questions == [VIVA_ITEM] and note == "Short notes."


# --- Quiz validation ---

def test_quiz_valid():
    assert validate_quiz([QUIZ_ITEM] * 5) == [QUIZ_ITEM] * 5


@pytest.mark.parametrize(
    "change",
    [
        {"options": ["A", "B", "C"]},  # only 3 options
        {"answer_index": 4},           # out of range
        {"answer_index": "1"},         # string, not int
        {"answer_index": True},        # bool sneaks through isinstance(int)
        {"explanation": None},
    ],
)
def test_quiz_bad_items_are_rejected(change):
    with pytest.raises(LLMError):
        validate_quiz([{**QUIZ_ITEM, **change}])


def test_quiz_must_be_a_list():
    with pytest.raises(LLMError):
        validate_quiz({"questions": [QUIZ_ITEM]})
