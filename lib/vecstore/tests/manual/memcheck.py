"""Manual memory check (loads the real models): drive a private daemon with bulk embeds and reranks and report its
peak physical footprint (what Activity Monitor shows as Memory: it includes the Metal buffers torch holds on MPS,
which `ps -o rss` does not see). Exits 1 when the peak is above --max-gb.

  VECSTORE_HOME=/tmp/vsx-mem VECSTORE_PYTHON=~/.cache/vecstore/venv/bin/python \
      python3 lib/vecstore/tests/manual/memcheck.py --batches 11 --reranks 50 --max-gb 5

It refuses to run against the shared daemon (~/.cache/vecstore) unless --allow-shared is given, and stops its own
daemon at the end. Texts are the longest rows of clu's committed KB export (the 2026-10-20 incident was a bulk embed
of long rows, sorted short to long, in requests of 128), cut to 2000 characters like clu's vector text.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

ASTACK = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ASTACK / "lib"))
from vecstore import client, config, procmem  # noqa: E402


def rss_mb(pid: int) -> float:
    """The daemon's physical footprint in MB (0 when it is gone)."""
    return float((procmem.usage(pid) or {}).get("footprint_mb", 0))


def _mem() -> str:
    m = (client.ping() or {}).get("mem") or {}
    return " ".join(f"{k}={v}" for k, v in m.items())


def texts(n: int) -> list[str]:
    rows = []
    for f in sorted((ASTACK / "clu" / "kb" / "export").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                rows.append(" ".join(str(v) for v in r.values() if isinstance(v, str))[:2000])
    rows.sort(key=len)
    return rows[-n:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=11, help="embed requests of --size texts each")
    ap.add_argument("--size", type=int, default=128)
    ap.add_argument("--reranks", type=int, default=50, help="rerank requests of 30 documents each")
    ap.add_argument("--max-gb", type=float, default=5.0)
    ap.add_argument("--kill-gb", type=float, default=9.0, help="kill the daemon above this (protects the machine)")
    ap.add_argument("--allow-shared", action="store_true")
    a = ap.parse_args(argv)
    if config.home() == Path.home() / ".cache" / "vecstore" and not a.allow_shared:
        print("memcheck: set VECSTORE_HOME to a private directory (or pass --allow-shared)", file=sys.stderr)
        return 2
    lifetime_peak = 0
    client.ensure_daemon(wait=180)
    pid = int(client.ping()["pid"])
    samples: list[tuple[float, str, float]] = []
    phase = ["loaded"]
    done = threading.Event()
    t0 = time.monotonic()

    def sample():
        while not done.is_set():
            samples.append((time.monotonic() - t0, phase[0], rss_mb(pid)))
            if samples[-1][2] > a.kill_gb * 1024:
                print(f"memcheck: daemon above {a.kill_gb} GB during {phase[0]}; killing it", file=sys.stderr)
                subprocess.run(["kill", str(pid)])
                return
            done.wait(0.5)
    threading.Thread(target=sample, daemon=True).start()
    try:
        tx = texts(a.batches * a.size)
        for i in range(a.batches):
            phase[0] = f"embed {i + 1}"
            t = time.monotonic()
            client.embed([config.doc_prefix() + x for x in tx[i * a.size:(i + 1) * a.size]], timeout=600)
            print(f"embed {i + 1}/{a.batches}: {time.monotonic() - t:.1f}s rss {rss_mb(pid):.0f} MB {_mem()}", flush=True)
        docs = tx[-30:]
        for i in range(a.reranks):
            phase[0] = f"rerank {i + 1}"
            t = time.monotonic()
            client.rerank(f"which table holds the promotion price, try {i}", docs, timeout=600)
            if i % 10 == 9 or i == 0:
                print(f"rerank {i + 1}/{a.reranks}: {time.monotonic() - t:.1f}s rss {rss_mb(pid):.0f} MB {_mem()}",
                      flush=True)
        phase[0] = "after"
        time.sleep(2)
        info = client.ping() or {}
        lifetime_peak = (procmem.usage(pid) or {}).get("peak_footprint_mb", 0)
    finally:
        done.set()
        client.stop()
    peak = max(samples, key=lambda s: s[2]) if samples else (0, "", 0)
    worst = max(peak[2], lifetime_peak)  # the kernel's lifetime maximum also catches spikes between samples
    phase_ = peak[1] if worst <= peak[2] + 50 else "between samples (kernel lifetime maximum)"
    print(json.dumps({"peak_mb": round(worst), "sampled_peak_mb": round(peak[2]), "peak_phase": phase_,
                      "end_mb": round(samples[-1][2]) if samples else 0, "daemon_mem": info.get("mem")}))
    return 1 if worst > a.max_gb * 1024 else 0


if __name__ == "__main__":
    raise SystemExit(main())
