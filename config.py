"""Central settings. Change values here or with environment variables."""
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(BASE_DIR, "agent.db")
HANDBOOK_PATH = os.path.join(DATA_DIR, "handbook.md")
STUDENTS_PATH = os.path.join(DATA_DIR, "students.json")
ELECTIVES_PATH = os.path.join(DATA_DIR, "electives.json")
TRACE_PATH = os.path.join(BASE_DIR, "logs", "traces.jsonl")

# Groq uses an OpenAI-compatible API. Keep overrides for other providers.
LLM_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
USE_MOCK = os.getenv("USE_MOCK", "auto")            # "auto" | "1" | "0"
STUDENT_DEMO_PASSWORD = os.getenv("STUDENT_DEMO_PASSWORD", "")
STAFF_DEMO_PASSWORD = os.getenv("STAFF_DEMO_PASSWORD", "")

# Agent mode:  "a1" = Assignment 1 baseline (no privacy / approval controls)
#              "a2" = Assignment 2 safe agent (identity, authorization, approval, audit)
AGENT_MODE = os.getenv("AGENT_MODE", "a2")

TOP_K_CHUNKS = 2          # how many policy chunks to send to the model
MIN_RETRIEVAL_SCORE = 0.20  # below this, policy "does not contain enough information"
MAX_HISTORY_TURNS = 4     # only recent turns go into the context
MAX_AGENT_STEPS = 5       # safety limit on the agent loop
