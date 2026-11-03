# vecstore

Local embeddings and vector search for agent-stack's agents and skills. It is a companion to each store's own keyword
search, never a replacement: every store keeps its keyword ranker, and vecstore adds a second ranking from
EmbeddingGemma 2 (text-only; Qwen3-Embedding-0.6B before 2026-10-07) and merges the two with reciprocal rank fusion. If anything in the vector path fails, the
search returns the keyword results and prints one line to stderr. Nothing leaves the machine.

Design: `docs/superpowers/specs/2026-09-30-vecstore-design.md`.

## How it works

- A daemon (`python3 -m vecstore.daemon`) keeps the model loaded and serves a private socket at
  `~/.cache/vecstore/embed.sock`. The first search after it has been idle for 20 minutes starts it again
  (about 20 s); a warm query embedding takes roughly 30-250 ms.
- Each store has one vector file next to its data (`*.vectors.sqlite`, gitignored). An entry is embedded again only
  when its text changes.
- Each store plugs in through a `vec_adapter.py` that subclasses `vecstore.hybrid.BaseAdapter`. Folders of
  markdown with no search command use `vecstore.mdstore.MdStore`.
- `--mode auto` uses vectors only when the store's recall baseline (`eval/baseline.json`) shows hybrid search at
  least matching keyword search on recall@5.

## Commands

```bash
python3 lib/vecstore/cli.py status               # daemon, and per store: vectors, stale, auto mode
python3 lib/vecstore/cli.py index <store>        # embed new or changed entries (--rebuild to start over, --quiet)
python3 lib/vecstore/cli.py search <store> "<q>" # --k N, --type T, --mode auto|lexical|hybrid, --json
python3 lib/vecstore/cli.py eval <store>         # --save-baseline, --check
python3 lib/vecstore/cli.py daemon start|stop|ping
```

## Switches

`VECSTORE_MODEL` picks the embedding model (default google/embeddinggemma-2; Qwen/Qwen3-Embedding-0.6B is still supported). Each vector file records its model;
`sync` re-embeds a file built with another model, search never mixes models, and a non-default model gets its own
daemon socket. Each model family has a profile in config.py (prompts, pooling, token limit): Qwen pools the last token and takes the store's instruction; EmbeddingGemma uses sentence-transformers mean pooling and its task prompts. Only one model's daemon runs at a time: starting one stops the others. The daemon runs from ~/.cache/vecstore/venv when it exists.
`VECSTORE_DISABLE=1` turns vectors off everywhere. `VECSTORE_IDLE_MIN` sets the idle exit (default 20).
`VECSTORE_RERANK_IDLE_MIN` sets when an unused reranker is unloaded (default 10).
`VECSTORE_HOME` moves the socket, lock and log (default `~/.cache/vecstore`).
`VECSTORE_REGISTRY` points at an alternative registry file. `VECSTORE_PYTHON` is the interpreter used to start the daemon.

clu's own `kb.py search --mode auto` uses vectors whenever its vector file exists, while `vecstore search` applies the
recall-baseline gate.

## Retrieval upgrade (2026-10-20)

- `normalize.py` folds Arabic and Persian spellings (Arabic yeh and kaf to Persian, tatweel and diacritics removed,
  Persian and Arabic-Indic digits to ASCII, ZWNJ kept but cleaned, whitespace collapsed). It is applied to the text a
  store indexes (vector text, MdStore keyword body) and to every query. `VECSTORE_NORMALIZE=0` turns it off; an
  index built under one setting must be queried under the same, so rebuild after switching.
- `rerank.py` and the daemon's `rerank` endpoint reorder the fused top 30 with BAAI/bge-reranker-v2-m3 (a
  sentence-transformers CrossEncoder, loaded on the first request in the daemon venv only). Any failure keeps the
  fused order, silently. `VECSTORE_RERANK=on|off|auto` (auto = only when the store's `baseline.json` has a
  `hybrid_rerank` entry with recall@5 and MRR at least equal to `hybrid`), `VECSTORE_RERANK_TOP` (30),
  `VECSTORE_RERANK_BLEND=order|rrf` (rrf = 0.7 / reranker rank + 0.3 / fused rank), `VECSTORE_RERANK_MODEL`.
  A store opts in by implementing `rerank_text(eid)` on its adapter (MdStore already does).
- `dedupe.py` clusters near-duplicate vectors by cosine; it reports and never merges.

## Memory (2026-10-21)

On 2026-10-20 the daemon grew to 14 GB during a bulk embed and the Mac went into swap. Two causes, both measured with
`tests/manual/memcheck.py` (it drives a private daemon and reads the physical footprint, which `ps` RSS misses
because the Metal buffers torch holds on MPS are not resident pages):

- The MPS allocator kept every forward pass's buffers (about 1 GB per batch of 16 long texts) and texts of new lengths
  rarely reused them: 12 GB of Metal memory after ten requests of 128 rows. The daemon now calls
  `torch.mps.empty_cache()` after every batch (`release_memory()`).
- MPS compiles a graph per input shape and keeps it (about 45 MB per new shape). Batches are now padded to a multiple
  of 64 tokens (`bucket_padding()`), so a bulk embed sees a handful of shapes. Padding is masked; vectors differ from
  the old ones only at fp16 noise level (cosine >= 0.996, the same as changing the batch size), so no re-embed.

The reranker is loaded straight in fp16, and dropped after `VECSTORE_RERANK_IDLE_MIN` minutes (default 10) without a
rerank request; the next one loads it again (about 10 s). `daemon ping` reports the footprint, its peak, what MPS
holds and whether the reranker is loaded. Measured peak for 1,408 long rows plus 50 reranks: 15 GB before (killed
before the reranks), 4.96 GB after; 2.0-3.2 GB while embedding, 4.1 GB with the reranker loaded.

```bash
VECSTORE_HOME=/tmp/vsx-mem python3 lib/vecstore/tests/manual/memcheck.py --max-gb 5   # loads the real models
```

## Tests

`python3 -m pytest lib/vecstore/tests -q`. The tests use a fake hashing encoder and never load the model.
