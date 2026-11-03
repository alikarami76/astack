"""Model name, file locations and environment switches shared by every vecstore module."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

MODEL = "google/embeddinggemma-2"  # the default since 2026-10-07; VECSTORE_MODEL picks another one
QWEN = "Qwen/Qwen3-Embedding-0.6B"  # the model before 2026-10-07; still supported
MAX_TOKENS = 384  # Qwen's limit (kept so its old vectors stay valid)

# How each model family wants its inputs. Qwen takes the store's own instruction on queries and plain documents,
# pooled from the last token. EmbeddingGemma takes fixed task prompts (model card, "SearchQuery"/"Document") and
# mean pooling through sentence-transformers, loaded text-only (vision and audio encoders off: 270M parameters).
PROFILES = {
    "qwen": dict(query_prompt=None, doc_prompt="", max_tokens=384, kind="last-token"),
    "gemma": dict(query_prompt="task: search result | query: ", doc_prompt="title: none | text: ", max_tokens=512,
                  kind="sentence-transformers"),
}
LIB = Path(__file__).resolve().parents[1]
ASTACK = LIB.parent
_FRAMEWORK_PY = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"


def model() -> str:
    """The configured embedding model. Each vector file records the model it was built with; a file built with
    another model is re-embedded by sync and never mixed into a search."""
    return os.environ.get("VECSTORE_MODEL") or MODEL


def profile(m: str | None = None) -> dict:
    return PROFILES["gemma" if "embeddinggemma" in (m or model()).lower() else "qwen"]


def query_prefix(store_instruct: str) -> str:
    """The text put before a query: the store's own instruction for Qwen, the model's task prompt for Gemma."""
    p = profile()["query_prompt"]
    return store_instruct if p is None else p


def doc_prefix() -> str:
    return profile()["doc_prompt"]


def _suffix() -> str:
    """The default model keeps the original socket names; another model gets its own daemon, so two sessions with
    different models never restart each other's daemon."""
    m = model()
    return "" if m == MODEL else "-" + hashlib.sha1(m.encode()).hexdigest()[:8]


def home() -> Path:
    return Path(os.environ.get("VECSTORE_HOME") or Path.home() / ".cache" / "vecstore")


def sock_path() -> Path:
    return home() / f"embed{_suffix()}.sock"


def lock_path() -> Path:
    return home() / f"daemon{_suffix()}.lock"


def log_path() -> Path:
    return home() / f"daemon{_suffix()}.log"


def rerank_idle_minutes() -> float:
    """The daemon drops the reranker (about 1.2 GB) after this many minutes without a rerank request and loads it
    again on the next one (about 10 s). VECSTORE_RERANK_IDLE_MIN, default 10."""
    try:
        return float(os.environ.get("VECSTORE_RERANK_IDLE_MIN", "10"))
    except ValueError:
        return 10.0


def idle_minutes() -> float:
    return float(os.environ.get("VECSTORE_IDLE_MIN", "20"))


def disabled() -> bool:
    return os.environ.get("VECSTORE_DISABLE") == "1"


def venv_python() -> Path:
    return home() / "venv" / "bin" / "python"


def daemon_python() -> str:
    """The daemon needs torch, transformers >= 5.19 and sentence-transformers. On the user's Mac they live in
    ~/.cache/vecstore/venv (built on the framework Python with --system-site-packages, for torch)."""
    if os.environ.get("VECSTORE_PYTHON"):
        return os.environ["VECSTORE_PYTHON"]
    for p in (venv_python(), Path(_FRAMEWORK_PY)):
        if p.exists():
            return str(p)
    return sys.executable


def normalizing() -> bool:
    """Farsi/Arabic normalisation (normalize.py) of indexed text and queries. VECSTORE_NORMALIZE=0 turns it off, so
    the eval can compare with and without it (an index built under one setting must be queried under the same)."""
    return os.environ.get("VECSTORE_NORMALIZE", "1") != "0"


RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_TOP = 30  # fused candidates sent to the reranker
RERANK_BLEND = "rrf"  # "order" = the reranker's order; "rrf" = reciprocal rank, 0.7 reranker + 0.3 fused
RERANK_WEIGHT = 0.7
RERANK_MAX_TOKENS = 256  # per (query, document) pair; 512 doubled the latency on the 2026-10-20 eval


def rerank_model() -> str:
    return os.environ.get("VECSTORE_RERANK_MODEL") or RERANK_MODEL


def rerank_flag() -> str:
    """VECSTORE_RERANK: on, off or auto (the default: rerank only when the store's eval baseline shows it at least
    as good as plain hybrid search)."""
    v = os.environ.get("VECSTORE_RERANK", "auto").strip().lower()
    return {"1": "on", "on": "on", "true": "on", "0": "off", "off": "off", "false": "off"}.get(v, "auto")


def rerank_top() -> int:
    try:
        return max(2, int(os.environ.get("VECSTORE_RERANK_TOP", RERANK_TOP)))
    except ValueError:
        return RERANK_TOP


def rerank_blend() -> str:
    v = os.environ.get("VECSTORE_RERANK_BLEND", RERANK_BLEND).strip().lower()
    return v if v in ("order", "rrf") else RERANK_BLEND


def rerank_max_tokens() -> int:
    try:
        return max(64, int(os.environ.get("VECSTORE_RERANK_MAX_TOKENS", RERANK_MAX_TOKENS)))
    except ValueError:
        return RERANK_MAX_TOKENS
