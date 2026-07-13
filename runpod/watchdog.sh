#!/usr/bin/env bash
# Idle-stop must NOT count while the fleet is still LOADING. Weight download is not GPU
# work, so util sits at 0% for the entire ~38GB pull — the ungated idle rule would stop
# the pod mid-download and we would pay to do the whole thing again. Gate the idle rule
# on the fleet actually being up (/workspace/.fleet-ready). The HARD TTL runs from t=0
# and stays UNGATED: that is the layer that catches a load which hangs forever.
set -uo pipefail
START=$(date +%s); IDLE_STREAK=0; LOG=/workspace/conclave-watchdog.log
MAXLIFE="${MAX_LIFETIME_MIN:-120}"; IDLEMAX="${IDLE_MIN:-20}"
K=$(tr -d '[:space:]' < /workspace/.runpod_key)
P=$(tr '\0' '\n' < /proc/1/environ | grep '^RUNPOD_POD_ID=' | cut -d= -f2-)
kill_pod() {
  echo "$(date -u +%FT%TZ) STOPPING POD ($1)" >> "$LOG"
  curl -s --max-time 30 -X POST "https://api.runpod.io/graphql?api_key=$K" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"mutation { podStop(input: {podId: \\\"$P\\\"}) { id desiredStatus } }\"}" >> "$LOG" 2>&1
  sleep 30; pkill -f vllm || true; pkill -f litellm || true
}
while true; do
  NOW=$(date +%s); ELAPSED=$(( (NOW-START)/60 ))
  if [ "$ELAPSED" -ge "$MAXLIFE" ]; then kill_pod "hard TTL ${ELAPSED}min"; exit 0; fi
  U=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1); U=${U:-0}
  if [ ! -f /workspace/.fleet-ready ]; then
    IDLE_STREAK=0
    echo "$(date -u +%FT%TZ) util=${U}% LOADING (idle gated) elapsed=${ELAPSED}/${MAXLIFE}min" >> "$LOG"
  else
    if [ "$U" -lt 5 ]; then IDLE_STREAK=$((IDLE_STREAK+1)); else IDLE_STREAK=0; fi
    echo "$(date -u +%FT%TZ) util=${U}% idle=${IDLE_STREAK}/${IDLEMAX}min elapsed=${ELAPSED}/${MAXLIFE}min" >> "$LOG"
    if [ "$IDLE_STREAK" -ge "$IDLEMAX" ]; then kill_pod "idle ${IDLEMAX}min"; exit 0; fi
  fi
  sleep 60
done
