#!/usr/bin/env bash
# Build clu's causal toolkit environment: Python 3.12 venv, pinned packages, gbstats at a pinned commit.
set -euo pipefail
CLU="$(cd "$(dirname "$0")/../.." && pwd)"
V="$CLU/.venv-causal"
GB_COMMIT=f0589f62c6b8e10fa669cdb35751a7fa58389b24
command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
[ -x "$V/bin/python" ] || uv venv -q --python 3.12 "$V"
VIRTUAL_ENV="$V" uv pip install -q -r "$CLU/scripts/causal/requirements.txt"
if ! "$V/bin/python" -c "import gbstats" 2>/dev/null; then
  # gbstats on PyPI is stale (0.8.0, 2024); install packages/stats from git. --no-deps because its metadata
  # pins numpy<2 and pandas<3, which it does not actually need.
  SRC="$V/src/growthbook"; rm -rf "$SRC"; mkdir -p "$SRC"
  git -C "$SRC" init -q
  git -C "$SRC" remote add origin https://github.com/growthbook/growthbook.git
  git -C "$SRC" sparse-checkout set packages/stats
  git -C "$SRC" fetch -q --depth 1 --filter=blob:none origin "$GB_COMMIT"
  git -C "$SRC" checkout -q FETCH_HEAD
  VIRTUAL_ENV="$V" uv pip install -q --no-deps "$SRC/packages/stats"
fi
"$V/bin/python" -c "import diff_diff, pyfixest, rdrobust, rddensity, gbstats, statsmodels" && echo "causal toolkit ready: $V"
