"""Central configuration for the THD Website Knowledge Assistant."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------- Paths ----------
DATA_DIR = BASE_DIR / "data"
PDF_DIR = DATA_DIR / "pdfs"
RAW_PAGES = DATA_DIR / "raw_pages.jsonl"      # one extracted record per line
CHUNKS_FILE = DATA_DIR / "chunks.jsonl"       # chunked corpus (used for BM25)
CRAWL_DB = DATA_DIR / "crawl_state.sqlite3"   # resumable crawl state
CHROMA_DIR = Path(os.getenv("CHROMA_DIR", DATA_DIR / "chroma_v2"))
COLLECTION = "thd_knowledge"

# ---------- Crawler ----------
START_URLS = [
    "https://www.th-deg.de/en",
    "https://www.th-deg.de/en/study-with-us",
    "https://www.th-deg.de/study-programmes",
    "https://www.th-deg.de/int-students",
    "https://www.th-deg.de/en/apply",
    "https://www.th-deg.de/campus-cham-en",
]
ALLOWED_HOSTS = {"www.th-deg.de", "th-deg.de"}
CANONICAL_HOST = "www.th-deg.de"
ALLOWED_LANGS = {"en"}               # add "de" to also index German content
MAX_HTML_PAGES = int(os.getenv("MAX_HTML_PAGES", "2500"))
MAX_PDFS = int(os.getenv("MAX_PDFS", "100"))
MAX_PDF_MB = 8
REQUEST_TIMEOUT = 20
REQUEST_DELAY = 0.25                 # politeness delay per worker (seconds)
WORKERS = 6
USER_AGENT = "THD-Knowledge-Assistant/1.0 (student internship project; educational use)"
SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".mp4", ".mp3",
    ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".ics", ".css", ".js",
)
SKIP_PATH_PARTS = ("/secure/", "/admin", "/login", "/search", "/suche")

# ---------- Chunking / retrieval ----------
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
TOP_K = 8
CANDIDATES = 40                      # candidates from each retriever before fusion
MAX_CHUNKS_PER_URL = 3

# ---------- LLM (Groq, OpenAI-compatible endpoint) ----------
LLM_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")
LLM_TEMPERATURE = 0.2
