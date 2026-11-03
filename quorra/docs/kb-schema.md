# Knowledge base schema

The KB is a SQLite database (`kb/kb.sqlite`, rebuildable) whose source of truth is the JSONL export in `kb/export/<type>.jsonl`. Code lives in `kb/store.py`; the table definitions are in `kb/schema.sql`. Every table also carries `id`, `tags` (JSON list), `origin`, `created_at` and `updated_at`.

The `id` is `<prefix>_<first 12 hex of sha1>` of the natural key, with each key part lowercased and whitespace-collapsed. Upserting the same natural key updates the row; empty values never overwrite stored ones. A row whose natural key parts are all empty is rejected.

Values that are lists or objects are stored as JSON text.

## Allowed values

- `tier`: `enforced`, `dominant`, `transitional`, `stated_only`
- `source`: `code`, `tooling`, `review`, `guide`

`store.upsert` and `store.rebuild` reject a convention whose tier or source is outside these lists.

## convention (table `conventions`, id prefix `conv`)

Natural key: `repo, scope, area, rule`. `scope` is empty for a rule found across the whole repo, or the module path prefix (for example `src/Voucher/`) when a large repo is researched per module; an empty scope is left out of the id hash, so whole-repo rules keep the id of `repo, area, rule`.
Columns: `repo` (a repo name, or `shared-rules`), `scope`, `area`, `rule`, `tier`, `rationale`, `examples`, `counter_examples`, `source`, `checked_at_sha`, `verified` (0 or 1), `superseded_by` (id of the replacing rule; superseded rows are hidden from search and rulebooks), `run_id`.

`examples` and `counter_examples` are lists of `{file, line, snippet}`:

```json
[{"file": "src/Service/A.php", "line": 3, "snippet": "class AService"}]
```

## concept (table `concepts`, id prefix `cpt`)

Natural key: `name_en`.
Columns: `name_en`, `names`, `description`, `implementations`, `kb_docs`, `verified_at_sha`.

`names` maps language to a list of strings; `en`, `fa` and `codename` are the keys used for query expansion:

```json
{"en": ["cart"], "fa": ["سبد خرید"], "codename": ["basket"]}
```

`implementations` is a list of objects that must each have `repo` and `file`; `symbol` and `kind` are optional:

```json
[{"repo": "api-app", "file": "src/Service/CartService.php", "symbol": "CartService", "kind": "class"}]
```

`kb_docs` is a list of paths into the feature knowledge base.

## lesson (table `lessons`, id prefix `les`)

Natural key: `rule`.
Columns: `rule`, `why`, `trigger_quote` (the user's words that caused the lesson), `run_id`, `applies_to`.

## exception (table `exceptions`, id prefix `exc`)

Natural key: `repo, rule_id, situation`.
Columns: `repo`, `rule_id`, `situation`, `decision`, `why`, `run_id`.

## run (table `runs_index`, id prefix `run`)

Natural key: `run_id`.
Columns: `run_id`, `date`, `type`, `request`, `repos` (comma-joined), `outcome`, `corrections_count`, `verify_result` (JSON text of the last verification entry). Written by `run_record close`; see `docs/run-schema.md`.

## Research files (`kb/ingest_rules.py`)

The research run writes three JSON files into its run folder. `ingest_rules.py` loads them into the KB and exports the `convention` and `lesson` types afterwards.

### rules.json (`ingest_rules.py ingest <rules.json>`)

```json
{"repo": "pricing", "scope": "", "sha": "<40-hex>", "run_id": "2026-09-30-research-pricing",
 "rules": [{"area": "data-storage", "rule": "Declare tables in getTableSchemaDefinition() with TableSchema",
            "tier": "dominant", "source": "code", "rationale": "...",
            "examples": [{"file": "src/Entity/A.php", "line": 12, "snippet": "new TableSchema("}],
            "counter_examples": [], "evidence": {"files_following": 41, "files_checked": 45,
                                                 "recent_following": 12, "recent_checked": 12}}]}
```

Each rule is validated on its own; a failing rule is reported in `rejected` with a reason and the others are still ingested. A rule needs an `area`, a `rule`, and a `tier` and `source` from the allowed sets. A `code` rule needs at least 2 examples, other sources at least 1. Every example file must exist at `sha` and its `line` must lie within the file. A `review` rule may cite `{"file": "reviews.jsonl", "line": n}`, which skips the file check but still needs `line >= 1`. Snippets are scrubbed and cut to 160 characters. `evidence` values must be integers (a numeric string is coerced; anything else rejects the rule) and are appended to the rationale as ` Evidence: 41/45 files, 12/12 recent.` In a scoped document (a module run), `source: tooling`, `source: review` and `tier: enforced` rules are rejected: they belong to the whole-repo pass. Rejected rule text is scrubbed. `ingested` lists only ids that are live after the ingest; an old wording kept by a rewording or a shared rule is listed under `unchanged_via_rewording`. Ingested rows get `verified = 0`, `checked_at_sha = sha` and `run_id`.

### rules-revised.json (revision loop; same document fields as rules.json)

Every revised rule carries `"replaces": "<old id>"`, the id of the refuted rule it revises. After upserting, `ingest` calls `supersede(<old id>, <new id>)` when the ids differ (a rewording), which carries the old rule's `refuted:` tags over; when the ids are equal (same wording, perhaps a new tier) nothing more happens. A `replaces` that is not a known convention id of the same repo, or that is no longer live, rejects that rule with a reason; the others still ingest. A rule the synthesizer gives up is listed as `{"drop": "<id>", "reason": "..."}` and is dropped exactly as `ingest_rules.py drop` does. The whole file is checked before anything is written: two rules with the same `replaces` (a split), an id that is both in a `replaces` and in a `drop`, or an id dropped twice is a document error (exit 1, nothing written). To split a rule, keep `replaces` on one rule and add the others as new rules without it. `replaces` must name a rule of the same repo and scope. The ingest output adds `"superseded": [[old, new], ...]` and `"dropped": [...]`; `plan` adds `"supersedes"` and `"drop"`. `plan` on this partial file lists every other live rule of the repo and scope under `missing`; that is expected, and `--retire-missing` must never be used with it.

`ingest_rules.py drop <id> --run-id R --reason "..."` sets `superseded_by = "dropped:R"` and records a lesson `Dropped convention: <rule>` (`why` = `given up after refutation: <reason>`). A dropped or retired rule that a later run finds again is revived, and so is an old wording whose rewording was later dropped or retired: `superseded_by` and its old `refuted:` tags are cleared and `verified = 0`.

### verdicts.json (`ingest_rules.py verdicts <verdicts.json> --run-id R`)

```json
[{"id": "conv_ab12...", "verdict": "sustained", "evidence": "src/Entity/B.php:9 also follows"},
 {"id": "conv_cd34...", "verdict": "refuted", "evidence": "..."}]
```

`sustained` sets `verified = 1`. `refuted` adds the tag `refuted:<run_id>:<round>` (`--round`, default 1). A convention with refutations from 2 distinct rounds of the current run (tags with another run id never count; re-finding a retired or dropped rule clears them) gets `superseded_by = "dropped:<run_id>"` and a lesson `Dropped convention: <rule>` (`why` = `refuted twice: <evidence>`, `applies_to` = `<repo>/<area>`).

### consistency.json (`ingest_rules.py share <consistency.json> --run-id R`)

```json
[{"rule": "...shared wording...", "area": "naming", "members": ["conv_...", "conv_..."]}]
```

Each group with at least 2 existing members becomes a `shared-rules` convention. Its tier is the weakest member tier (order, strongest first: enforced > dominant > transitional > stated_only), so the shared rule is never more binding than it is in any member repo. It merges up to 3 examples, each with the file prefixed by its repo (`api-app:src/...`). It is `verified = 1` only when every member is verified. Each member gets `superseded_by` set to the new id.
