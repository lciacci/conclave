#!/usr/bin/env bash
# Conclave fleet boot — RunPod edition. Runs INSIDE the pod.
#
# Ported from infra/user-data.sh.tftpl. Same fleet, same additive memory slices, same
# sequential start, same LiteLLM gateway. What changes is everything AWS-shaped:
#
#   EC2 + cloud-init      -> a RunPod pod running this script
#   docker run per model  -> a vLLM PROCESS per model (a RunPod pod IS a container;
#                            docker-in-docker is not available, so we drop the
#                            container-per-model layer and run the servers directly)
#   EFS weight cache      -> /workspace (RunPod network volume), or a fresh HF pull
#   SSM Parameter Store   -> env vars injected at pod creation
#   Tailscale             -> SSH tunnel from the laptop (nothing binds to 0.0.0.0;
#                            see runpod/README for the port-forward). Deliberate,
#                            reversible: the orchestrator's ONLY coupling is
#                            $CONCLAVE_GW, so switching back is a one-line change.
#   CloudWatch idle-stop  -> the watchdog below. THIS IS THE PART THAT MATTERS.
#
# THE COST SAFETY MODEL, stated plainly, because AWS's out-of-band kill switch does
# NOT survive this move and pretending otherwise would be the dangerous lie:
#
#   AWS stopped a wedged box because CLOUDWATCH did the stopping — an authority
#   OUTSIDE the box. RunPod has no equivalent. Everything here runs ON the pod, so a
#   pod wedged hard enough to kill this watchdog will keep billing. Three layers, in
#   order of trustworthiness:
#
#     1. PREPAID CREDIT BALANCE (outside the box, hard, absolute). Keep it small.
#        This is the only guard that survives total pod failure. It is your real cap.
#     2. HARD TTL (below). Kills the pod after MAX_LIFETIME_MIN no matter what — even
#        if the fleet never loads, even if vLLM crash-loops. The AWS box once
#        crash-looped vLLM for hours while billing; an idle-only rule does NOT catch
#        that, because a crash-looping box is not idle. This does.
#     3. IDLE RULE (below). Stops the pod after IDLE_MIN of GPU util < 5%.
#
#   The watchdog starts BEFORE the models load, on purpose: a boot that hangs during
#   model loading must still die on the TTL.
#
# Required env (injected at pod creation):
#   RUNPOD_API_KEY   — used ONLY to terminate this pod. Nothing else reads it.
#   RUNPOD_POD_ID    — set automatically by RunPod.
#   HF_TOKEN         — Gemma is a gated repo. Without this, `general` will not load.
# Optional:
#   MAX_LIFETIME_MIN — hard TTL, default 120.
#   IDLE_MIN         — idle-stop window, default 20.
set -euo pipefail

# RunPod injects the pod's env vars into PID 1 ONLY. An SSH session gets a fresh
# environment from sshd and inherits NONE of them — so RUNPOD_API_KEY, HF_TOKEN and
# RUNPOD_POD_ID are all empty when this script is run over SSH, even though they are
# correctly configured on the pod. Without this, the kill-switch check below fails
# closed and refuses to boot a perfectly good pod. Pull anything missing from PID 1.
for v in RUNPOD_API_KEY RUNPOD_POD_ID HF_TOKEN MAX_LIFETIME_MIN IDLE_MIN; do
  eval "cur=\${$v:-}"
  if [ -z "$cur" ] && [ -r /proc/1/environ ]; then
    val=$(tr '\0' '\n' < /proc/1/environ | grep "^$v=" | cut -d= -f2- || true)
    [ -n "$val" ] && export "$v=$val"
  fi
done

# A pod's environment is FIXED AT CONTAINER START: editing the RunPod secret does not
# reach a running pod. So a bad/underscoped key cannot be rotated without rebuilding
# the pod — unless there is an override. /workspace/.runpod_key is that override, and
# it WINS over the env var. (It lives on the persistent volume, mode 0600.)
if [ -r /workspace/.runpod_key ]; then
  RUNPOD_API_KEY="$(tr -d '[:space:]' < /workspace/.runpod_key)"
  export RUNPOD_API_KEY
  echo "using RUNPOD_API_KEY from /workspace/.runpod_key (overrides pod env)"
fi
# Same override for the HF token. `general` (Gemma-2-9b) is a GATED repo: with no token
# it simply does not download, and you get a SILENT two-model fleet — which voids the
# experiment while looking like it worked. The file override also lets you fix a missing
# or rotated token on a RUNNING pod: a pod's env is fixed at container start, so the
# alternative is a rebuild.
if [ -r /workspace/.hf_token ]; then
  HF_TOKEN="$(tr -d '[:space:]' < /workspace/.hf_token)"
  export HF_TOKEN
  echo "using HF_TOKEN from /workspace/.hf_token (overrides pod env)"
fi

MAX_LIFETIME_MIN="${MAX_LIFETIME_MIN:-120}"
IDLE_MIN="${IDLE_MIN:-20}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# FLEET_JSON overrides the fleet. The judge experiments need ONE model on the card (the
# candidates are frozen on disk — there is nothing for the other two to do, and paying to
# load 38GB of weights to run a 6GB judge is money set on fire). Defaults to the full
# 3-model fleet so every existing invocation is unchanged.
FLEET="${FLEET_JSON:-$HERE/fleet.json}"
LOG=/workspace/conclave-boot.log
mkdir -p /workspace
exec > >(tee -a "$LOG") 2>&1

echo "=== conclave boot $(date -u +%FT%TZ) ==="

# ---------------------------------------------------------------------------
# 0. VERIFY THE KILL SWITCH BEFORE SPENDING A SINGLE GPU-MINUTE.
#    A GPU whose teardown path is unproven is the $712/month failure. If we cannot
#    demonstrate, right now, that this pod can terminate itself, we do not load the
#    models — we exit. FAIL CLOSED. The cost of being wrong here is unbounded; the
#    cost of exiting early is about one minute of GPU time.
# ---------------------------------------------------------------------------
: "${RUNPOD_API_KEY:?RUNPOD_API_KEY is required — it is how this pod kills itself}"
: "${RUNPOD_POD_ID:?RUNPOD_POD_ID is required (RunPod normally injects it)}"

rp_gql() {  # $1 = graphql query/mutation string. Key goes in the query string (RunPod docs).
  curl -s --max-time 20 -X POST "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(printf '{"query":%s}' "$(printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')")"
}

# Prove BOTH halves of the kill switch before spending GPU time:
#   (a) the key authenticates at all, and
#   (b) THIS pod id is visible to that key — a valid key for the wrong account would
#       authenticate happily and then be unable to stop this pod.
echo "verifying the kill switch..."
probe="$(rp_gql 'query { myself { id pods { id } } }' || true)"
if ! printf '%s' "$probe" | grep -q '"id"'; then
  echo "FATAL: cannot authenticate to the RunPod API with RUNPOD_API_KEY."
  echo "       This pod would have NO WAY TO STOP ITSELF. Refusing to load models."
  echo "       API said: $probe"
  echo "       STOP THIS POD FROM THE RUNPOD CONSOLE NOW, then fix the key."
  exit 1
fi
if ! printf '%s' "$probe" | grep -q "$RUNPOD_POD_ID"; then
  echo "FATAL: the API key authenticates, but pod $RUNPOD_POD_ID is not among its pods."
  echo "       The watchdog could not stop this pod. Refusing to load models."
  echo "       STOP THIS POD FROM THE RUNPOD CONSOLE NOW."
  exit 1
fi
echo "kill switch verified: key authenticates AND can see pod $RUNPOD_POD_ID"

# ---------------------------------------------------------------------------
# 1. WATCHDOG — started FIRST, so a hung model load still dies on the TTL.
# ---------------------------------------------------------------------------
cat > /usr/local/bin/conclave-watchdog.sh <<'WDEOF'
#!/usr/bin/env bash
# Self-terminate on (a) hard TTL, or (b) sustained GPU idle. Runs on the pod.
# Layer 2 and 3 of the cost model — see boot.sh. Layer 1 (prepaid credits) is the
# only one that survives this script dying, which is why the balance must be small.
set -uo pipefail
START=$(date +%s)
IDLE_STREAK=0
LOG=/workspace/conclave-watchdog.log

kill_pod() {
  echo "$(date -u +%FT%TZ) STOPPING POD ($1)" >> "$LOG"
  # podStop is the DOCUMENTED mutation and it RELEASES THE GPU — which is ~99% of
  # the cost. (A `podTerminate` mutation is NOT in RunPod's docs; do not guess at an
  # undocumented call for a safety-critical path. The residual volume charge after a
  # stop is pennies.) The key goes in the QUERY STRING, which is what RunPod's docs
  # specify — not a Bearer header.
  curl -s --max-time 30 -X POST \
    "https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"mutation { podStop(input: {podId: \\\"$RUNPOD_POD_ID\\\"}) { id desiredStatus } }\"}" \
    >> "$LOG" 2>&1

  sleep 30
  # Belt and braces. If the GPU is still ours 30s later the stop did not take, so
  # kill the fleet: a pod with no models loaded costs GPU-hours but cannot silently
  # keep *working*, and it makes the failure obvious instead of expensive.
  echo "$(date -u +%FT%TZ) verifying stop took effect..." >> "$LOG"
  pkill -f "vllm" || true
  pkill -f "litellm" || true
  # Full teardown (releases the volume too) if the CLI happens to be present.
  command -v runpodctl >/dev/null && runpodctl remove pod "$RUNPOD_POD_ID" >> "$LOG" 2>&1 || true
}

while true; do
  NOW=$(date +%s)
  ELAPSED_MIN=$(( (NOW - START) / 60 ))

  # (b) HARD TTL — fires regardless of what the fleet is doing. Catches the case an
  # idle rule cannot: a crash-looping vLLM is NOT idle, and would bill forever.
  if [ "$ELAPSED_MIN" -ge "$MAX_LIFETIME_MIN" ]; then
    kill_pod "hard TTL: ${ELAPSED_MIN}min >= ${MAX_LIFETIME_MIN}min"
    exit 0
  fi

  # (c) IDLE — GPU util under 5% for IDLE_MIN consecutive minutes.
  U=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1)
  U=${U:-0}
  if [ "$U" -lt 5 ]; then
    IDLE_STREAK=$((IDLE_STREAK + 1))
  else
    IDLE_STREAK=0
  fi
  echo "$(date -u +%FT%TZ) util=${U}% idle_streak=${IDLE_STREAK}/${IDLE_MIN}min elapsed=${ELAPSED_MIN}/${MAX_LIFETIME_MIN}min" >> "$LOG"

  if [ "$IDLE_STREAK" -ge "$IDLE_MIN" ]; then
    kill_pod "idle: GPU <5% for ${IDLE_MIN}min"
    exit 0
  fi
  sleep 60
done
WDEOF
# The heredoc above is a FALLBACK. runpod/watchdog.sh is the real one and it SUPERSEDES
# it: the inline copy's idle rule is UNGATED, and an ungated idle rule stops the pod
# mid-weight-download (util is 0% for the whole ~38GB pull, because downloading is not
# GPU work). That bug fired on a live boot. If watchdog.sh shipped alongside this
# script, use it.
if [ -r "$HERE/watchdog.sh" ]; then
  cp "$HERE/watchdog.sh" /usr/local/bin/conclave-watchdog.sh
  echo "watchdog: using runpod/watchdog.sh (idle rule GATED on fleet-ready)"
fi
chmod +x /usr/local/bin/conclave-watchdog.sh
MAX_LIFETIME_MIN="$MAX_LIFETIME_MIN" IDLE_MIN="$IDLE_MIN" \
  RUNPOD_API_KEY="$RUNPOD_API_KEY" RUNPOD_POD_ID="$RUNPOD_POD_ID" \
  setsid nohup /usr/local/bin/conclave-watchdog.sh </dev/null >/dev/null 2>&1 &
echo "watchdog live: hard TTL ${MAX_LIFETIME_MIN}min, idle-stop ${IDLE_MIN}min"

# ---------------------------------------------------------------------------
# 2. GPU sanity — the fleet's FP8 member needs Ada/Hopper (compute cap >= 8.9).
#    On Ampere vLLM silently downgrades FP8 to W8A16/Marlin: it still WORKS, so
#    nothing would fail loudly, and the eval numbers would quietly stop being
#    comparable to the frozen run. Refuse instead.
# ---------------------------------------------------------------------------
CC=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d '.')
GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)
echo "GPU: $GPU (compute capability $CC)"
if [ "${CC:-0}" -lt 89 ]; then
  echo "FATAL: $GPU is compute cap $CC (< 8.9 = pre-Ada)."
  echo "       reasoning is an FP8 checkpoint; vLLM would SILENTLY fall back to"
  echo "       W8A16/Marlin — different numerics, results not comparable to the"
  echo "       frozen base-36 run. Use an L40S / L40 / RTX 6000 Ada / H100."
  kill_now=1
fi
if [ "${kill_now:-0}" = "1" ]; then
  echo "       stopping the pod rather than producing incomparable numbers."
  rp_gql "mutation { podStop(input: {podId: \"$RUNPOD_POD_ID\"}) { id desiredStatus } }" || true
  exit 1
fi

# ---------------------------------------------------------------------------
# 3. THE FLEET — one vLLM process per model, SEQUENTIALLY.
#    Sequential start is REQUIRED, not stylistic: vLLM checks FREE >= mem_util*TOTAL
#    at each process's startup and reserves that slice, so each free-check must run
#    against real predecessor residency. Starting them in parallel races the checks
#    and a later model dies with "less than desired GPU memory utilization".
#    --enforce-eager on every model: frees CUDA-graph memory for KV and avoids the
#    capture spike — doubly important with 3 processes sharing one card.
# ---------------------------------------------------------------------------
export HF_HOME=/workspace/hf
mkdir -p "$HF_HOME"
command -v jq >/dev/null || (apt-get update -qq && apt-get install -y -qq jq)

# ---------------------------------------------------------------------------
# 3a. INSTALL vLLM — MATCHED TO THE HOST DRIVER.
#
# This script never installed vLLM at all: it assumed the image shipped it, and on the
# H100 boot someone had pip-installed it BY HAND. So a fresh pod reached the model-start
# loop with no vllm module, every server died with ModuleNotFoundError, and the boot
# reported success anyway (see the fail-closed fix below). Install it here, explicitly.
#
# THE DRIVER RULE IN THE OLD HANDOFF WAS WRONG. It said "driver >= CUDA 12.8". An L4 with
# driver 570 / CUDA **12.8** was rejected by vllm 0.24 with:
#     RuntimeError: The NVIDIA driver on your system is too old (found version 12080)
# because vllm 0.24's wheel is built against torch 2.11+**cu130** and its kernels link
# libcudart.so.13 — so it needs CUDA **13.0** (driver >= 580), not 12.8. Swapping torch to
# a cu128 build does NOT help: vllm's own compiled .so still wants libcudart.so.13.
#
# So: read the host's CUDA version and pick a vLLM built for it. The driver is a HOST
# property — no image and no pip flag can change it.
CUDA_VER=$(nvidia-smi | grep -o 'CUDA Version: [0-9.]*' | awk '{print $3}')
CUDA_MAJ=${CUDA_VER%%.*}
# FAIL CLOSED on a parse failure. If nvidia-smi output is malformed, CUDA_VER is empty and the
# ${CUDA_MAJ:-0} default silently routes to the CUDA-12 branch — which would install vllm 0.11
# on a CUDA-13 host, fail to load, and waste ~10 min before the fleet-failed check kills the pod.
# The pod is GPU-less garbage if we cannot even read the driver; kill it now, not after a load.
case "$CUDA_MAJ" in
  ''|*[!0-9]*) kill_pod "cannot parse CUDA version from nvidia-smi (got '$CUDA_VER')"; exit 1;;
esac
echo "host CUDA: $CUDA_VER (driver $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1))"

if python3 -c 'import vllm' 2>/dev/null; then
  echo "vllm already present: $(python3 -c 'import vllm; print(vllm.__version__)')"
elif [ "${CUDA_MAJ:-0}" -ge 13 ]; then
  echo "CUDA >= 13 -> vllm 0.24.0 (cu130). This is the frozen fleet's stack."
  pip install --break-system-packages -q vllm==0.24.0 || { kill_pod "vllm install failed"; exit 1; }
else
  # CUDA 12.x: vllm 0.24 CANNOT run here. 0.11.0 is the last line built on torch cu128.
  # transformers MUST be pinned below 5: vllm 0.11 declares `transformers>=4.55.2` with no
  # upper bound, pip resolves that to 5.x, and 5.x dropped the tokenizer attribute vllm
  # reads -> "GemmaTokenizer has no attribute all_special_tokens_extended". Unbounded
  # dependency, silent major-version bump, dead boot.
  echo "CUDA $CUDA_VER (<13) -> vllm 0.11.0 (cu128) + transformers<5"
  pip install --break-system-packages -q "vllm==0.11.0" "transformers<5" \
    || { kill_pod "vllm install failed"; exit 1; }
fi
python3 -c 'import vllm' 2>/dev/null || { kill_pod "vllm not importable after install"; exit 1; }

# ---------------------------------------------------------------------------
# 3b. START THE MODELS. A model that does not come up is a FAILED BOOT, not a warning.
rm -f /workspace/.fleet-failed
jq -c '.[]' "$FLEET" | while read -r m; do
  name=$(echo "$m" | jq -r .name)
  repo=$(echo "$m" | jq -r .repo)
  port=$(echo "$m" | jq -r .port)
  util=$(echo "$m" | jq -r .mem_util)
  maxlen=$(echo "$m" | jq -r .max_len)
  dtype=$(echo "$m" | jq -r .dtype)
  dtype_arg=""
  [ -n "$dtype" ] && [ "$dtype" != "null" ] && dtype_arg="--dtype $dtype"

  echo "--- starting $name ($repo) on :$port at util $util"
  nohup python3 -m vllm.entrypoints.openai.api_server \
    --model "$repo" \
    --served-model-name "$name" \
    --download-dir "$HF_HOME" \
    --host 127.0.0.1 --port "$port" \
    --max-model-len "$maxlen" \
    --gpu-memory-utilization "$util" \
    $dtype_arg \
    --enforce-eager \
    > "/workspace/vllm-$name.log" 2>&1 &

  # Wait for THIS model before starting the next. Bounded (~10 min) so one stuck
  # model cannot hang the boot forever — and the watchdog's TTL backstops even that.
  for _ in $(seq 1 120); do
    if grep -q "Application startup complete" "/workspace/vllm-$name.log" 2>/dev/null; then
      echo "$name ready"; break
    fi
    sleep 5
  done
  # FAIL CLOSED. This used to print a WARNING and carry on, and then /workspace/.fleet-ready
  # was touched UNCONDITIONALLY at the end — so a boot where NOT ONE MODEL LOADED reported
  # success, armed the idle rule, and left a GPU billing with nothing serving. That is the
  # "silent two-model fleet" this file warns about elsewhere, in its worst form.
  # NB the `while read` body is a SUBSHELL (jq | while), so a variable set here would not
  # survive the loop — the failure must be recorded on disk.
  grep -q "Application startup complete" "/workspace/vllm-$name.log" 2>/dev/null \
    || { echo "FATAL: $name did not start — see /workspace/vllm-$name.log"
         echo "$name" >> /workspace/.fleet-failed; }
done

if [ -s /workspace/.fleet-failed ]; then
  kill_pod "fleet FAILED to start: $(tr '\n' ' ' < /workspace/.fleet-failed)— refusing to bill a GPU that serves nothing"
  exit 1
fi

# ---------------------------------------------------------------------------
# 4. LiteLLM gateway — one OpenAI-compatible endpoint in front of all three.
#    Generated from the SAME fleet.json = single source of truth. Binds to
#    127.0.0.1: nothing is publicly reachable; the laptop reaches it over an SSH
#    tunnel. LITELLM_LOG=DEBUG is the only level that emits per-request
#    response_cost (verified on AWS 2026-07-08).
# ---------------------------------------------------------------------------
pip install -q "litellm[proxy]" 2>&1 | tail -1 || true
{
  echo "model_list:"
  jq -c '.[]' "$FLEET" | while read -r m; do
    name=$(echo "$m" | jq -r .name)
    port=$(echo "$m" | jq -r .port)
    echo "  - model_name: $name"
    echo "    litellm_params:"
    echo "      model: openai/$name"
    echo "      api_base: http://127.0.0.1:$port/v1"
    echo "      api_key: none"
    echo "      input_cost_per_token: $(echo "$m" | jq -r .cost_in)"
    echo "      output_cost_per_token: $(echo "$m" | jq -r .cost_out)"
  done
  echo "litellm_settings:"
  echo "  success_callback: [\"logging\"]"
  echo "  drop_params: true"
} > /workspace/litellm-config.yaml

LITELLM_LOG=DEBUG nohup litellm --config /workspace/litellm-config.yaml \
  --host 127.0.0.1 --port 4000 > /workspace/litellm.log 2>&1 &

for _ in $(seq 1 60); do
  curl -sf http://127.0.0.1:4000/health/liveliness >/dev/null 2>&1 && break
  sleep 2
done

echo
echo "=== FLEET UP ==="
curl -s http://127.0.0.1:4000/v1/models 2>/dev/null | head -c 400 || true
echo
# ARM THE IDLE RULE. The watchdog deliberately ignores GPU-idle until this file exists,
# because weight download is not GPU work and an ungated idle rule would stop the pod
# mid-download. Nothing creates this but here — if boot.sh never reaches this line, only
# the hard TTL protects the pod, which is exactly the intended behaviour for a failed boot.
touch /workspace/.fleet-ready
echo "idle rule ARMED (/workspace/.fleet-ready)"
echo "watchdog: hard TTL ${MAX_LIFETIME_MIN}min | idle-stop ${IDLE_MIN}min | log /workspace/conclave-watchdog.log"
echo "from the laptop:  ssh -N -L 4000:127.0.0.1:4000 <pod-ssh>   then  CONCLAVE_GW=localhost:4000"
