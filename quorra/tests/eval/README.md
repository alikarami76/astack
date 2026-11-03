# Lookup Goldens

Hand-checked test cases for the quorra lookup evaluation harness. The public repo ships no goldens, because they name files in a specific codebase: write your own for your repos (`eval_lookup.py draft` proposes candidates from the concept map).

## Golden Format

Each golden is one JSON object per line in `lookup_goldens.jsonl`:

```json
{
  "id": "g01",
  "query": "...",
  "phrasing": "en|fa|codename|description",
  "repos": ["pricing"],
  "expect": [
    {
      "repo": "pricing",
      "file": "src/...",
      "symbol": "Name"
    }
  ]
}
```

The `symbol` field is optional. A hit counts at rank r (1-based) when r ≤ 5, the hit's `repo` and `file` match an expected entry, and the name equals the expected `symbol` if one is given.

## Selecting Goldens

Goldens are chosen with the user to ensure coverage across:
- Multiple repositories
- Multiple query phrasings (English, Persian, codename, description)
- At least 8 queries with Persian or codename phrasings out of the target set of about 30

## Pass Criteria

The lookup evaluation passes when the **top-5 hit rate is at least 90%** across all goldens.

Rerun `eval_lookup.py run` after any change to `scripts/qindex/` or `kb/store.py` to verify the implementation continues to meet this threshold.

## Commands

- **Draft new goldens from concepts:** `python3 scripts/eval_lookup.py draft --out tests/eval/lookup_goldens.draft.jsonl`
- **Evaluate against goldens:** `python3 scripts/eval_lookup.py run --goldens tests/eval/lookup_goldens.jsonl --min 0.9`
