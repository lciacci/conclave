#!/usr/bin/env bash
# Launch Aider driven by the LOCAL Qwen coder (qwen3-coder:30b), talking straight to
# Ollama — NO LiteLLM proxy. Aider is the lighter-harness counterpart to run-local-cc.sh:
# it sends a repo map + only the files you /add, not Claude Code's ~15k-token prompt every
# turn. That is the whole point of this build — CC was measured prefill-bound/slow, and the
# open question was "is that the harness or the model?". Same model, lighter harness = the
# controlled test.
#
#   harness/run-local-aider.sh [aider args...]   # launch aider on the local coder
#
# Aider needs no background daemon; when you quit aider, nothing is left running.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"   # uv-installed aider lives here

command -v aider >/dev/null || { echo "ERROR: aider not on PATH. Install: uv tool install --python 3.13 --with audioop-lts aider-chat"; exit 1; }

# Ollama must be up (it serves the model).
curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 || { echo "ERROR: Ollama not running (start Ollama first)."; exit 1; }

echo ">>> Launching Aider on LOCAL qwen3-coder:30b (Ollama direct, no proxy)."
echo ">>> num_ctx pinned to 32768 via harness/aider.model.settings.yml (Ollama's 2048 default silently truncates)."
echo ">>> Weaker model: keep auto-commits ON (default) so every edit is a revertable commit, and read diffs before /run."

OLLAMA_API_BASE=http://127.0.0.1:11434 \
  aider \
    --model ollama_chat/qwen3-coder:30b \
    --model-settings-file "${HERE}/aider.model.settings.yml" \
    "$@"
