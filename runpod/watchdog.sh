#!/usr/bin/env bash
# Idle-stop must NOT count while the fleet is still LOADING. Weight download is not GPU
# work, so util sits at 0% for the entire ~38GB pull — the ungated idle rule would stop
# the pod mid-download and we would pay to do the whole thing again. Gate the idle rule
# on the fleet actually being up (/workspace/.fleet-ready). The HARD TTL runs from t=0
# and stays UNGATED: that is the layer that catches a load which hangs forever.
set -uo pipefail
START=$(date +%s); IDLE_STREAK=0; LOG=/workspace/conclave-watchdog.log
MAXLIFE="${MAX_LIFETIME_MIN:-120}"; IDLEMAX="${IDLE_MIN:-20}"

# CREDENTIALS — THE BUG THIS BLOCK EXISTS TO FIX.
# This script used to read the key ONLY from /workspace/.runpod_key. But boot.sh treats that
# file as an OPTIONAL OVERRIDE: it verifies the kill switch with $RUNPOD_API_KEY (harvested
# from /proc/1/environ) and exports it — which this script then IGNORED. With no file present,
# `set -uo pipefail` (no -e) left K="" and podStop 401'd. The watchdog would then pkill vLLM
# and exit 0: TTL fires, THE POD NEVER STOPS, and the GPU bills at idle until the credit
# balance drains. Fail-closed was defeated — boot.sh proved key X works, the watchdog used an
# empty key Y. Take the key from the SAME places boot.sh does, and REFUSE TO RUN without one:
# a watchdog that cannot kill is worse than no watchdog, because it looks like protection.
K="${RUNPOD_API_KEY:-}"
P="${RUNPOD_POD_ID:-}"
[ -r /workspace/.runpod_key ] && K=$(tr -d '[:space:]' < /workspace/.runpod_key)
[ -z "$K" ] && K=$(tr '\0' '\n' < /proc/1/environ 2>/dev/null | grep '^RUNPOD_API_KEY=' | cut -d= -f2-)
[ -z "$P" ] && P=$(tr '\0' '\n' < /proc/1/environ 2>/dev/null | grep '^RUNPOD_POD_ID=' | cut -d= -f2-)
if [ -z "$K" ] || [ -z "$P" ]; then
  echo "$(date -u +%FT%TZ) FATAL: no kill-switch credentials (key=${#K} chars, pod='${P}')." >> "$LOG"
  echo "FATAL: watchdog has NO WAY TO STOP THIS POD. Refusing to run — stop it from the console."
  exit 1
fi

kill_pod() {
  echo "$(date -u +%FT%TZ) STOPPING POD ($1)" >> "$LOG"
  # RETRY, and VERIFY. A single fire-and-forget POST was the old behaviour: it could 401, or
  # the request could simply fail, and nothing noticed. Do not exit until the pod reports
  # EXITED — an unverified stop is not a stop.
  for attempt in 1 2 3 4 5; do
    r=$(curl -s --max-time 30 -X POST "https://api.runpod.io/graphql?api_key=$K" \
      -H "Content-Type: application/json" \
      -d "{\"query\":\"mutation { podStop(input: {podId: \\\"$P\\\"}) { id desiredStatus } }\"}" 2>&1)
    echo "$(date -u +%FT%TZ) podStop attempt $attempt: $r" >> "$LOG"
    case "$r" in *EXITED*) echo "$(date -u +%FT%TZ) POD STOPPED." >> "$LOG"; return 0;; esac
    sleep 15
  done
  # Every attempt failed. Make the pod useless rather than let it quietly bill: kill the fleet,
  # try the CLI, and KEEP LOOPING (the caller must NOT exit) so a transient outage still stops
  # the pod when the API comes back.
  echo "$(date -u +%FT%TZ) podStop FAILED 5x — halting the fleet and retrying" >> "$LOG"
  pkill -f vllm || true
  pkill -f litellm || true
  command -v runpodctl >/dev/null && runpodctl remove pod "$P" >> "$LOG" 2>&1 || true
  return 1
}
while true; do
  NOW=$(date +%s); ELAPSED=$(( (NOW-START)/60 ))
  # NEVER `exit` on a FAILED stop — a watchdog that gives up leaves the GPU billing. kill_pod
  # returns 0 only when the pod actually reports EXITED; otherwise we loop and try again.
  if [ "$ELAPSED" -ge "$MAXLIFE" ]; then
    kill_pod "hard TTL ${ELAPSED}min" && exit 0
    sleep 60; continue
  fi

  # nvidia-smi can return "N/A" (driver hiccup, or the GPU is being reset). `[ "$U" -lt 5 ]`
  # then dies with an integer-expression error and — because there is no `set -e` — the idle
  # streak silently NEVER accumulates, so idle-stop quietly stops working. Treat any
  # non-numeric reading as "cannot tell", which must NOT count as idle.
  U=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
  case "${U:-}" in ''|*[!0-9]*) U=-1;; esac

  if [ ! -f /workspace/.fleet-ready ]; then
    IDLE_STREAK=0
    echo "$(date -u +%FT%TZ) util=${U}% LOADING (idle gated) elapsed=${ELAPSED}/${MAXLIFE}min" >> "$LOG"
  else
    if [ "$U" -ge 0 ] && [ "$U" -lt 5 ]; then IDLE_STREAK=$((IDLE_STREAK+1)); else IDLE_STREAK=0; fi
    echo "$(date -u +%FT%TZ) util=${U}% idle=${IDLE_STREAK}/${IDLEMAX}min elapsed=${ELAPSED}/${MAXLIFE}min" >> "$LOG"
    if [ "$IDLE_STREAK" -ge "$IDLEMAX" ]; then
      kill_pod "idle ${IDLEMAX}min" && exit 0
      sleep 60; continue
    fi
  fi
  sleep 60
done
