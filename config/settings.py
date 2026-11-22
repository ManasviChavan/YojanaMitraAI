import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.6-flash")
GEMINI_EMBEDDING_MODEL = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini" if GEMINI_API_KEY else "local").strip().lower()
VECTOR_STORE_PATH = Path(os.getenv("VECTOR_STORE_PATH", "data/vector_store"))
if not VECTOR_STORE_PATH.is_absolute():
    VECTOR_STORE_PATH = ROOT / VECTOR_STORE_PATH
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "data/yojanamitra.sqlite3"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = ROOT / DATABASE_PATH
SOURCES_PATH = ROOT / "config" / "trusted_sources.json"
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "12"))
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", "5000000"))
MAX_HTML_TEXT_CHARS = int(os.getenv("MAX_HTML_TEXT_CHARS", "150000"))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "60"))
MAX_CRAWL_PAGES = int(os.getenv("MAX_CRAWL_PAGES", "12"))
MAX_PAGES_PER_SOURCE = int(os.getenv("MAX_PAGES_PER_SOURCE", "2"))
MIN_REQUEST_INTERVAL_SECONDS = float(os.getenv("MIN_REQUEST_INTERVAL_SECONDS", "0.5"))
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "48"))
