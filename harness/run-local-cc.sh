#!/usr/bin/env bash
# Launch Claude Code driven by the LOCAL Qwen coder (qwen3-coder:30b) instead of Claude,
# via a LiteLLM Anthropic<->Ollama proxy. Your normal `claude` sessions are unaffected.
#
#   harness/run-local-cc.sh            # starts the proxy if needed, then launches claude
#   harness/run-local-cc.sh --stop     # stop the background proxy
#
# The proxy stays running in the background across sessions; --stop kills it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PORT=4000
URL="http://localhost:${PORT}"
LOG=/tmp/conclave-litellm.log
PIDFILE=/tmp/conclave-litellm.pid

if [[ "${1:-}" == "--stop" ]]; then
  [[ -f "$PIDFILE" ]] && kill "$(cat "$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE" && echo "proxy stopped." || echo "no proxy pid on file."
  exit 0
fi

# 1. Ollama must be up (it serves the model).
curl -sf http://localhost:11434/api/tags >/dev/null || { echo "ERROR: Ollama not running (start Ollama first)."; exit 1; }

# 2. Start the proxy if it isn't already answering.
if ! curl -sf "${URL}/health/liveliness" >/dev/null 2>&1; then
  echo "starting LiteLLM proxy on :${PORT} (log: ${LOG}) ..."
  "${HERE}/venv/bin/litellm" --config "${HERE}/litellm_config.yaml" --port "${PORT}" >"${LOG}" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 1 40); do curl -sf "${URL}/health/liveliness" >/dev/null 2>&1 && break; sleep 1; done
  curl -sf "${URL}/health/liveliness" >/dev/null 2>&1 || { echo "ERROR: proxy did not come up — check ${LOG}"; exit 1; }
fi
echo "proxy up on ${URL}."

# 3. Launch Claude Code with the LOCAL Qwen as the brain.
#    IMPORTANT: drive it in prompt-mode (approve each tool call) — you observe every
#    choice Qwen makes, and a weaker model can't run Bash/Edit unwatched.
echo ">>> Launching Claude Code on LOCAL qwen3-coder:30b. Approve tool calls as they come."
echo ">>> Exit with Ctrl-C; the proxy keeps running (harness/run-local-cc.sh --stop to kill it)."
ANTHROPIC_BASE_URL="${URL}" \
ANTHROPIC_API_KEY="sk-conclave-local" \
ANTHROPIC_MODEL="qwen-local" \
ANTHROPIC_SMALL_FAST_MODEL="qwen-fast" \
  claude "$@"
