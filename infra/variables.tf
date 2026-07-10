variable "region" {
  type    = string
  default = "us-east-1"
}

variable "profile" {
  type    = string
  default = "yeti-conclave"
}

variable "monthly_cap_usd" {
  type    = string
  default = "100"
}

variable "alert_email" {
  type    = string
  default = "houseofyeti@gmail.com"
}

# Instance ids the idle-stop alarm watches. Empty until an instance exists;
# v1 adds the GPU instance id here (or its own module wires the alarm directly).
variable "watched_instances" {
  type    = list(string)
  default = []
}

variable "idle_minutes" {
  type    = number
  default = 30
}

# dev_mode widens the idle-stop window to 90m. The 30m default reaped the box
# mid-session 2026-07-08 during an inference-free stretch (boot debugging +
# co-residency restart-roulette look "idle" to a GPU-util alarm). 90m still
# reaps a genuinely-forgotten box, but survives a gnarly interactive boot.
# Boot dev work with -var dev_mode=true; leave false for unattended/normal boots.
variable "dev_mode" {
  type    = bool
  default = false
}

# GPU instance is gated: quota approval pending, and every apply that creates
# it starts billing. Flip with -var enable_gpu=true once quota lands.
variable "enable_gpu" {
  type    = bool
  default = false
}

variable "gpu_instance_type" {
  type    = string
  default = "g6e.xlarge"
}

# v1 launches on spot (only G quota approved as of 2026-07-06). Flip to false
# with -var once On-Demand G quota lands — no code change.
variable "use_spot" {
  type    = bool
  default = true
}

# Which AZ the GPU lands in. Spot capacity is per-AZ and volatile; us-east-1d
# was dry for g6e.xlarge on 2026-07-06. Flip with -var gpu_az=us-east-1c if the
# default AZ returns InsufficientInstanceCapacity. EFS has a mount target in
# every default subnet, so any AZ works for the model cache.
variable "gpu_az" {
  type    = string
  default = "us-east-1b"
}

# Model fleet, co-resident on one L40S. Each vLLM process gets mem_util (fraction
# of TOTAL 48 GB); the sum must stay under ~0.9 to leave system + CUDA headroom.
# v1 was a single 72B; v2 is 3 lineage-decorrelated specialists (see design.md).
# Gemma is HF-gated → needs a real token in the hf_token_param SSM param.
variable "models" {
  type = list(object({
    name     = string
    repo     = string
    port     = number
    mem_util = number
    max_len  = number
    dtype    = string # "" = model default; "float16" forces fp16 (AWQ kernels)
    # Per-model $/token for LiteLLM cost attribution. Self-hosted has no API
    # cost — the real basis is GPU-hours; these are amortization PLACEHOLDERS
    # (scaled by model size) so per-model spend is visible. Calibrate once we
    # measure throughput: $/token ≈ (GPU $/hr) / (tokens/hr) apportioned by use.
    cost_in  = number
    cost_out = number
  }))
  #
  # ARRAY ORDER = START ORDER; mem_util is a PER-SLICE ADDITIVE fraction, summing
  # to <~0.9 (MEASURED + FIXED 2026-07-10, v3 Chunk 2 boot). vLLM 0.24 checks, at
  # each process's startup, that FREE GPU mem >= mem_util * TOTAL, then reserves
  # that slice — the error is verbatim: "Free memory on device cuda:0 (7.19/44.39
  # GiB) on startup is less than desired GPU memory utilization (0.82, 36.4 GiB)".
  # So mem_util is a per-process REQUEST measured against currently-free memory,
  # NOT a cumulative ceiling on total. The earlier "cumulative 0.25/0.55/0.82"
  # scheme (untested until this boot) is therefore IMPOSSIBLE: the last starter
  # demands util*total FREE (reasoning at 0.82 wanted 36 GiB free with only 7
  # left) and crash-loops forever. Additive per-slice is correct, and the 2026-07-08
  # v2 boot that "verified" 3 models was in fact running additive utils, not the
  # cumulative reorder. Sequential start (user-data waits for each "Application
  # startup complete") is still REQUIRED: it makes each slice's free-check run
  # against real predecessor residency, avoiding a boot-time race. Size each slice
  # to weights + KV, order small->large so early free-checks pass easily:
  #   general   ~6 GB wt  -> 0.25 (11.1 GiB)   free-check vs 44 -> ok
  #   coder     ~9 GB wt  -> 0.30 (13.3 GiB)   free-check vs ~33 -> ok
  #   reasoning ~7.5 GB wt -> 0.24 (10.6 GiB)  free-check vs ~20 -> ok   (sum 0.79)
  # Verified this boot: all three startup=1, restarts=0, ~7.6 GiB headroom left.
  # If a model OOMs on load, nudge its slice up; if one wastes headroom, down.
  default = [
    # general (Gemma-2-9b INT4) starts first: smallest footprint, and it's the
    # default judge (decorrelated from the two Qwen candidates).
    { name = "general", repo = "hugging-quants/gemma-2-9b-it-AWQ-INT4", port = 8003, mem_util = 0.25, max_len = 8192, dtype = "", cost_in = 0.00000030, cost_out = 0.00000030 },
    # 14B not 32B: a 32B coder + 2 small models can't co-reside on one 48 GB L40S
    # (2026-07-07 — 32B+Gemma alone filled 42/44 GiB, no room for the 3rd). 14B
    # leaves headroom for all three. The 32B returns in v3 on its own GPU.
    { name = "coder", repo = "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ", port = 8001, mem_util = 0.30, max_len = 8192, dtype = "", cost_in = 0.00000040, cost_out = 0.00000040 },
    # R1-Distill-QWEN-7B (not Llama): the Llama distill leaks BPE byte markers
    # (Ġ/Ċ) under vLLM 0.24 — and the "try the V0 engine" hypothesis is now
    # FALSIFIED (2026-07-10, Chunk 2): jakiAJK Llama-8B AWQ under VLLM_USE_V1=0
    # STILL leaks Ġ/Ċ, so the bug is not V1-detokenizer-specific. Restoring a
    # Llama-lineage reasoner now waits on a future vLLM release or a different
    # model/quant — not an engine flag. The Qwen tokenizer decodes clean (coder
    # proves it). Trade: a 2nd Qwen member dents lineage decorrelation, accepted.
    # FP8-dynamic: reputable RedHat quant, native on the L40S (Ada), ~7.5 GB.
    { name = "reasoning", repo = "RedHatAI/DeepSeek-R1-Distill-Qwen-7B-FP8-dynamic", port = 8002, mem_util = 0.24, max_len = 8192, dtype = "", cost_in = 0.00000020, cost_out = 0.00000020 },
  ]
}

variable "tailscale_key_param" {
  type    = string
  default = "/conclave/tailscale-authkey"
}

# HF token for gated model downloads (e.g. Llama 3.3 70B). Empty/placeholder
# for ungated models like Qwen 2.5 72B AWQ — user-data skips -e HF_TOKEN then.
variable "hf_token_param" {
  type    = string
  default = "/conclave/hf-token"
}
