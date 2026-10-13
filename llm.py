"""The ONE place in the app that talks to the LLM.

We use the OpenAI Python SDK, but point it at Google Gemini's
OpenAI-compatible endpoint (or Groq's). Switching provider is only a change
in the .env file: the code stays the same.
"""

import json
import random
import re
import time

import openai

import config
from prompts import QA_PROMPT, QUIZ_PROMPT, VIVA_PROMPT


class LLMError(Exception):
    """Raised for any LLM problem. The message is safe to show to users."""


BUSY_MESSAGE = (
    "The AI service is busy, or the free limit is used up. Wait a minute and try again. "
    "If it keeps happening, switch to the Groq backup in .env."
)
FORMAT_MESSAGE = "The AI did not return the expected format. Try again."
JSON_RETRY_SUFFIX = "\n\nYour previous reply was not valid JSON. Reply again with ONLY valid JSON."


def _client() -> openai.OpenAI:
    """Build the API client from .env values (empty BASE_URL means OpenAI's default)."""
    if not config.API_KEY or not config.MODEL:
        raise LLMError("API key or model is missing. Check your .env file.")
    return openai.OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL or None, max_retries=0)


def _friendly_status_error(error: openai.APIStatusError) -> LLMError:
    """Turn an HTTP error from the provider into a message a student understands."""
    if error.status_code in (400, 401, 403):
        return LLMError("The AI service rejected the request. Check API_KEY in your .env file.")
    if error.status_code == 404:
        return LLMError("Model not found. Update MODEL in your .env file from the provider's model list.")
    return LLMError(f"The AI service returned an error (HTTP {error.status_code}). Try again.")


def chat(prompt: str, temperature: float = config.TEMPERATURE) -> str:
    """Send one user message and return the reply text.
    Busy or network errors are retried once after 5 seconds."""
    client = _client()
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=config.MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
            return response.choices[0].message.content or ""
        except (openai.RateLimitError, openai.APIConnectionError):
            # Free tiers often hit rate limits: wait a bit and try one more time.
            if attempt == 0:
                time.sleep(5)
        except openai.APIStatusError as error:
            raise _friendly_status_error(error) from error
    raise LLMError(BUSY_MESSAGE)


def parse_json_reply(text: str) -> list | dict:
    """Strip ```json fences that models like to add, then parse the JSON.
    Raises ValueError if it is not valid JSON."""
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    return json.loads(cleaned)


def chat_json(prompt: str) -> list | dict:
    """Ask for JSON. If the reply is not valid JSON, ask once more, then give up."""
    reply = chat(prompt)
    try:
        return parse_json_reply(reply)
    except ValueError:
        reply = chat(prompt + JSON_RETRY_SUFFIX)
    try:
        return parse_json_reply(reply)
    except ValueError as error:
        raise LLMError(FORMAT_MESSAGE) from error


def _split_note(data: list | dict) -> tuple[list, str | None]:
    """The viva prompt may add a "note" field. Accept it either as a top-level
    {"note": ..., "questions": [...]} object or inside the list."""
    note = None
    if isinstance(data, dict):
        note = data.get("note")
        data = next((v for v in data.values() if isinstance(v, list)), [])
    if not isinstance(data, list):
        raise LLMError(FORMAT_MESSAGE)
    items = []
    for item in data:
        if isinstance(item, dict) and "note" in item:
            note = note or item["note"]
            if "question" not in item:
                continue  # an item that only carries the note
        items.append(item)
    return items, note


def validate_viva(data: list | dict) -> tuple[list[dict], str | None]:
    """Check every viva question has the keys the UI needs. Returns (questions, note)."""
    items, note = _split_note(data)
    for item in items:
        if not isinstance(item, dict):
            raise LLMError(FORMAT_MESSAGE)
        ok = (
            isinstance(item.get("question"), str)
            and isinstance(item.get("key_points"), list)
            and all(isinstance(p, str) for p in item["key_points"])
            and isinstance(item.get("model_answer"), str)
            and isinstance(item.get("marks"), int)
            and isinstance(item.get("source"), str)
        )
        if not ok:
            raise LLMError(FORMAT_MESSAGE)
    if not items and not note:
        raise LLMError(FORMAT_MESSAGE)
    return items, note


def validate_quiz(data: list | dict) -> list[dict]:
    """Check every quiz question has exactly 4 options and a valid answer index."""
    if not isinstance(data, list) or not data:
        raise LLMError(FORMAT_MESSAGE)
    for item in data:
        ok = (
            isinstance(item, dict)
            and isinstance(item.get("question"), str)
            and isinstance(item.get("options"), list)
            and len(item["options"]) == 4
            and all(isinstance(o, str) for o in item["options"])
            and type(item.get("answer_index")) is int  # bool is an int in Python, so be strict
            and 0 <= item["answer_index"] <= 3
            and isinstance(item.get("explanation"), str)
            and isinstance(item.get("source"), str)
        )
        if not ok:
            raise LLMError(FORMAT_MESSAGE)
    return data


def shuffle_options(quiz: list[dict], rng: random.Random | None = None) -> list[dict]:
    """Move each correct answer to a random position.

    QUIZ_PROMPT's format example shows "answer_index": 0, and the model sometimes
    copies it, putting every right answer in option A. That is the few-shot bias
    from Lecture 1. Shuffling in code guarantees the answer moves around.
    """
    rng = rng or random.Random()
    shuffled = []
    for q in quiz:
        order = list(range(4))
        rng.shuffle(order)
        options = [q["options"][i] for i in order]
        shuffled.append({**q, "options": options, "answer_index": order.index(q["answer_index"])})
    return shuffled


# --- The three features, each one prompt + one call ---

def answer_question(context: str, question: str) -> str:
    """Ask a doubt: answer from the notes only, with citations."""
    return chat(QA_PROMPT.format(college=config.COLLEGE_NAME, context=context, question=question)).strip()


def generate_viva(context: str, difficulty: str) -> tuple[list[dict], str | None]:
    """Viva questions: returns (questions, optional note from the examiner)."""
    return validate_viva(chat_json(VIVA_PROMPT.format(difficulty=difficulty, context=context)))


def generate_quiz(context: str) -> list[dict]:
    """Quiz me: returns 5 multiple-choice questions, with the options shuffled."""
    return shuffle_options(validate_quiz(chat_json(QUIZ_PROMPT.format(context=context))))
