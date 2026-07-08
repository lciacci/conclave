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
  # ARRAY ORDER = START ORDER, and mem_util is CUMULATIVE (2026-07-08, v3 Chunk 1).
  # vLLM's gpu_memory_utilization is a per-process CEILING on TOTAL GPU mem, not a
  # private slice — so with models co-resident, a later starter whose ceiling is
  # below the memory the earlier ones already hold computes NEGATIVE KV and dies
  # (`Available KV cache memory: -12.1 GiB`, reproduced both v2 boots). Additive
  # fractions summing to <1 are therefore WRONG here. Fix: start the models one at
  # a time (user-data waits for each "Application startup complete" before the
  # next) with ceilings that RISE to cover cumulative residency — each model's
  # util ≈ (everything resident once it loads) / 48 GB. Order small→large so early
  # ceilings stay low and leave room:
  #   general   ~5.5 GB wt + KV  -> ~10 GB cumulative -> 0.25
  #   coder     +9   GB wt + KV  -> ~24 GB cumulative -> 0.55
  #   reasoning +8   GB wt + KV  -> ~37 GB cumulative -> 0.82 (takes the remainder)
  # HYPOTHESIS pending the Chunk 2 boot — if a model still OOMs/KV-starves, nudge
  # its (and later) ceilings up; if one wastes headroom, down. The old additive
  # 0.24/0.26/0.36 "worked" only via restart-roulette (crash-loop until the race
  # happened to resolve); this is the deterministic replacement.
  default = [
    # general (Gemma-2-9b INT4) starts first: smallest footprint, and it's the
    # default judge (decorrelated from the two Qwen candidates).
    { name = "general", repo = "hugging-quants/gemma-2-9b-it-AWQ-INT4", port = 8003, mem_util = 0.25, max_len = 8192, dtype = "", cost_in = 0.00000030, cost_out = 0.00000030 },
    # 14B not 32B: a 32B coder + 2 small models can't co-reside on one 48 GB L40S
    # (2026-07-07 — 32B+Gemma alone filled 42/44 GiB, no room for the 3rd). 14B
    # leaves headroom for all three. The 32B returns in v3 on its own GPU.
    { name = "coder", repo = "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ", port = 8001, mem_util = 0.55, max_len = 8192, dtype = "", cost_in = 0.00000040, cost_out = 0.00000040 },
    # R1-Distill-QWEN-7B (not Llama): the Llama distill leaks BPE byte markers
    # (Ġ/Ċ) under vLLM 0.24's V1 detokenizer — reproduced across 2 quants, and
    # 0.24 is already the newest vLLM (no image bump available). The Qwen tokenizer
    # decodes clean (coder proves it). Trade: a 2nd Qwen member dents the lineage
    # decorrelation — acceptable for v2; restoring a Llama-lineage reasoner (V0
    # engine VLLM_USE_V1=0, or a future vLLM) is a v3 experiment. FP8-dynamic:
    # reputable RedHat quant, native on the L40S (Ada), ~8 GB, no AWQ-dtype hassle.
    # Starts last with the top ceiling (0.82) so it takes whatever KV remains.
    { name = "reasoning", repo = "RedHatAI/DeepSeek-R1-Distill-Qwen-7B-FP8-dynamic", port = 8002, mem_util = 0.82, max_len = 8192, dtype = "", cost_in = 0.00000020, cost_out = 0.00000020 },
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
