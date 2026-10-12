"""Settings for the Viva Question Bot.

Secrets and names come from the .env file (never hard-coded).
Tuning numbers live here so students can change them in one place.
"""

import os

from dotenv import load_dotenv

# Read the .env file (if it exists) into environment variables.
load_dotenv()

# --- LLM connection (read from .env) ---
API_KEY = os.getenv("API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
MODEL = os.getenv("MODEL", "")

# --- Names shown in the app header ---
COLLEGE_NAME = os.getenv("COLLEGE_NAME", "My College")
DEPARTMENT = os.getenv("DEPARTMENT", "Department of Computer Engineering")
COURSE = os.getenv("COURSE", "Prompt Engineering")

# --- RAG settings ---
CHUNK_SIZE = 800        # characters: small enough to be specific, big enough to hold a full idea
CHUNK_OVERLAP = 100     # characters: a sentence cut at a boundary still appears whole in one chunk
TOP_K_QA = 4            # chunks retrieved for "Ask a doubt"
TOP_K_GENERATE = 6      # chunks used for viva and quiz generation
EMBED_MODEL = "all-MiniLM-L6-v2"  # small (~90 MB) embedding model that runs on a CPU

# --- Demo limits ---
MAX_FILES = 5
MAX_PAGES_TOTAL = 150
MIN_CHARS_PER_PAGE = 30  # below this, the page is probably a scanned image

# --- LLM behaviour ---
TEMPERATURE = 0.2  # low = consistent, factual output
