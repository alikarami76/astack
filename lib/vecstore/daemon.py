"""The embedding daemon: loads the model once and serves embed requests on a private Unix socket.
Started on demand by client.py (python3 -m vecstore.daemon); exits after VECSTORE_IDLE_MIN idle minutes."""
from __future__ import annotations

import base64
import fcntl
import gc
import hashlib
import json
import os
import re
import socket
import sys
import time
from pathlib import Path

import numpy as np

from . import config, procmem


def release_memory() -> None:
    """Give the MPS allocator's cached buffers back to the system. Without this, every forward pass leaves its
    activations (about 1 GB for 16 texts of 350 tokens) cached, and texts of varying lengths rarely fit an old
    buffer, so the cache grew with every batch: 12 GB of Metal memory after ten requests of 128 long rows on
    2026-10-21 (memcheck.py), which put the Mac in swap. Costs no measurable time per batch."""
    torch = sys.modules.get("torch")
    if torch is not None and torch.backends.mps.is_available():
        torch.mps.empty_cache()


PAD_MULTIPLE = 64  # pad every batch to a multiple of this many tokens (see bucket_padding)


def bucket_padding(st_model, multiple: int = PAD_MULTIPLE) -> None:
    """Pad batches to a multiple of `multiple` tokens instead of to their longest text. MPS compiles and keeps a
    graph for every input shape it sees; with free padding every batch of a bulk embed had a new length, and the
    daemon's memory grew about 45 MB per new shape (2.2 to 4.0 GB over 768 long rows, flat when the same rows were
    embedded again; 2026-10-21). With buckets a model sees at most max_tokens / multiple lengths per batch size.
    Padding is masked, so the vectors do not change."""
    mods = list(st_model) if hasattr(st_model, "__iter__") else []
    for mod in mods or [getattr(st_model, "model", None)]:
        if mod is not None and hasattr(mod, "processing_kwargs"):
            pk = dict(mod.processing_kwargs or {})
            pk["text"] = {**(pk.get("text") or {}), "pad_to_multiple_of": multiple}
            mod.processing_kwargs = pk


class QwenEncoder:
    """Qwen3-Embedding exactly as clu/kb/vectors.py ran it: left padding, last-token pooling, L2 normalisation."""

    def __init__(self):
        os.environ["HF_HUB_OFFLINE"] = "1"  # the model is already cached; never call the Hub
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import torch
        from transformers import AutoModel, AutoTokenizer
        from transformers.utils import logging as tlog
        tlog.set_verbosity_error()
        tlog.disable_progress_bar()
        self.model = config.model()
        self.torch = torch
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(self.model, padding_side="left")
        self.m = AutoModel.from_pretrained(
            self.model, dtype=torch.float16 if self.dev == "mps" else torch.float32).to(self.dev).eval()
        self.dim = int(self.m.config.hidden_size)

    def encode(self, texts, batch=16):
        torch, out = self.torch, []
        for i in range(0, len(texts), batch):
            enc = self.tok(texts[i:i + batch], padding=True, truncation=True, max_length=config.MAX_TOKENS,
                           pad_to_multiple_of=PAD_MULTIPLE, return_tensors="pt").to(self.dev)
            with torch.no_grad():
                h = self.m(**enc).last_hidden_state[:, -1]  # left padding puts the last token at -1
            out.append(torch.nn.functional.normalize(h.float(), dim=-1).cpu().numpy())
            del enc, h
            release_memory()
        return np.vstack(out) if out else np.zeros((0, self.dim), dtype=np.float32)


class GemmaEncoder:
    """EmbeddingGemma through sentence-transformers: mean pooling and normalisation come from the model's own
    modules. Prompts are added by the callers (config.query_prefix / config.doc_prefix), so encode() takes plain
    text exactly like QwenEncoder."""

    def __init__(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import torch
        from sentence_transformers import SentenceTransformer
        self.model = config.model()
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.m = SentenceTransformer(self.model, device=self.dev,
                                     config_kwargs={"vision_config": None, "audio_config": None})
        self.m.max_seq_length = config.profile()["max_tokens"]
        bucket_padding(self.m)
        self.dim = int(self.m.get_sentence_embedding_dimension())

    def encode(self, texts, batch=16):
        """Same batches as sentence-transformers would form (longest texts first), one encode() call per batch so
        the MPS cache can be released in between."""
        texts = list(texts)
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        order = np.argsort([-len(t) for t in texts], kind="stable")
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i in range(0, len(texts), batch):
            idx = order[i:i + batch]
            out[idx] = self.m.encode([texts[j] for j in idx], batch_size=batch, normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False)
            release_memory()
        return out


class FakeEncoder:
    """Deterministic bag-of-words hashing encoder for tests (VECSTORE_FAKE_MODEL=1). Never used for real vectors."""
    model = "fake-hash-64"
    dim = 64

    def encode(self, texts, batch=16):
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w+", re.split(r"(?i)query: |text: ", t)[-1].lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(n == 0, 1, n)


class CrossEncoderReranker:
    """BAAI/bge-reranker-v2-m3 through sentence-transformers' CrossEncoder (relevance score in 0..1)."""

    def __init__(self):
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
        import torch
        from sentence_transformers import CrossEncoder
        self.model = config.rerank_model()
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        # fp16 on the GPU: about 1.8x faster, scores unchanged to 3 decimals. Loaded as fp16 directly: loading fp32
        # and halving afterwards briefly held both copies (+2.3 GB at the first rerank, memcheck 2026-10-21).
        self.m = CrossEncoder(self.model, device=dev, max_length=config.rerank_max_tokens(),
                              model_kwargs={"dtype": torch.float16} if dev == "mps" else None)
        bucket_padding(self.m)
        if dev == "mps":
            self.m.model.half()  # a no-op when the dtype above took effect

    def score(self, query, docs, batch=32):
        out = [float(x) for x in self.m.predict([(query, d) for d in docs], batch_size=batch,
                                                show_progress_bar=False)]
        release_memory()
        return out


class FakeReranker:
    """Word-overlap scorer for tests (VECSTORE_FAKE_MODEL=1)."""
    model = "fake-overlap"

    def score(self, query, docs, batch=16):
        q = set(re.findall(r"\w+", query.lower()))
        return [len(q & set(re.findall(r"\w+", d.lower()))) / (len(q) or 1) for d in docs]


def _load_reranker():
    return FakeReranker() if os.environ.get("VECSTORE_FAKE_MODEL") == "1" else CrossEncoderReranker()


class RerankerSlot:
    """Holds the reranker: loaded on the first rerank request (a daemon that only embeds never pays for the second
    model) and dropped after idle_s seconds without one (config.rerank_idle_minutes), then loaded again on demand."""

    def __init__(self, loader=None, idle_s: float | None = None, clock=time.monotonic, release=None):
        self.loader = loader or _load_reranker
        self.idle_s = config.rerank_idle_minutes() * 60 if idle_s is None else idle_s
        self.clock = clock
        self.release = release or release_memory
        self.model = None
        self.last = 0.0

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def get(self):
        if self.model is None:
            self.release()  # start the load from an empty MPS cache
            self.model = self.loader()
            gc.collect()  # the CPU copy of the weights the load went through
            self.release()
        self.last = self.clock()
        return self.model

    def maybe_unload(self) -> bool:
        if self.model is None or self.clock() - self.last < self.idle_s:
            return False
        self.model = None
        gc.collect()
        self.release()
        return True


_SLOT = RerankerSlot()


def get_reranker():
    return _SLOT.get()


def make_encoder():
    if os.environ.get("VECSTORE_FAKE_MODEL") == "1":
        return FakeEncoder()
    return GemmaEncoder() if config.profile()["kind"] == "sentence-transformers" else QwenEncoder()


def _log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def mem_stats() -> dict:
    """Physical footprint (current and peak) and, when torch is loaded on MPS, what its allocator holds (for memcheck and the log)."""
    import resource
    out = {**(procmem.usage() or {"maxrss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1 << 20))}),
           "reranker_loaded": _SLOT.loaded}
    torch = sys.modules.get("torch")
    if torch is not None and torch.backends.mps.is_available():
        out["mps_alloc_mb"] = round(torch.mps.current_allocated_memory() / (1 << 20))
        out["mps_driver_mb"] = round(torch.mps.driver_allocated_memory() / (1 << 20))
    return out


def handle(enc, req: dict) -> dict:
    op = req.get("op")
    if op == "ping":
        return {"model": enc.model, "dim": enc.dim, "pid": os.getpid(), "mem": mem_stats()}
    if op == "embed":
        vecs = np.ascontiguousarray(enc.encode(list(req["texts"])), dtype=np.float32)
        return {"model": enc.model, "dim": enc.dim, "vecs": base64.b64encode(vecs.tobytes()).decode()}
    if op == "rerank":
        rr = get_reranker()
        return {"model": rr.model, "scores": rr.score(req["query"], list(req["docs"]))}
    if op == "stop":
        return {"stopping": True}
    return {"error": f"unknown op {op!r}"}


def serve(enc, sock_path: Path, idle_s: float) -> None:
    sock_path.unlink(missing_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    os.chmod(sock_path, 0o600)
    srv.listen(16)
    srv.settimeout(min(30.0, idle_s))
    last = time.monotonic()
    _log(f"serving {enc.model} on {sock_path}")
    try:
        while True:
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                if _SLOT.maybe_unload():
                    _log("reranker unloaded after idle")
                if time.monotonic() - last >= idle_s:
                    _log("idle, exiting")
                    return
                continue
            last = time.monotonic()
            with conn:
                conn.settimeout(120)
                t0 = time.monotonic()
                try:
                    req = json.loads(b"".join(iter(lambda: conn.recv(1 << 16), b"")))
                    if req.get("op") != "rerank" and _SLOT.maybe_unload():  # a busy daemon never times out
                        _log("reranker unloaded after idle")
                    resp = handle(enc, req)
                except Exception as e:  # noqa: BLE001 - a bad request must not kill the daemon
                    req, resp = {}, {"error": f"{type(e).__name__}: {e}"}
                try:
                    conn.sendall(json.dumps(resp).encode())
                except OSError:  # the client gave up (timeout) before the answer: keep serving
                    _log("client went away before the reply")
                if req.get("op") != "ping":
                    fp = (procmem.usage() or {}).get("footprint_mb", "?")
                    _log(f"{req.get('op')} n={len(req.get('texts') or req.get('docs') or [])} "
                         f"ms={(time.monotonic() - t0) * 1000:.0f} mem_mb={fp}")
            if resp.get("stopping"):
                _log("stop requested, exiting")
                return
    finally:
        srv.close()
        sock_path.unlink(missing_ok=True)


def main() -> int:
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    lock = open(config.lock_path(), "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # another daemon owns the socket
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    serve(make_encoder(), config.sock_path(), config.idle_minutes() * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
