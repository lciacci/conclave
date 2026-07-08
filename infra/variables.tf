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
  default = [
    # 14B not 32B: a 32B coder + 2 small models can't co-reside on one 48 GB L40S
    # (2026-07-07 — 32B+Gemma alone filled 42/44 GiB, no room for the 3rd). 14B
    # leaves headroom for all three. The 32B returns in v3 on its own GPU.
    # Utils bumped 0.66 -> 0.86 sum (2026-07-08): at 0.28/0.20/0.18 every model
    # died on load with `_check_enough_kv_cache_memory` (No available memory for
    # cache blocks) — each slice ~= its own weights, ~0 left for KV, while 34% of
    # the 48 GB sat unused. Co-resident vLLM accounts each util as a fraction of
    # TOTAL GPU mem, so late-starting members see others' weights against their
    # budget; the coder lost the KV race twice before winning. 0.36/0.26/0.24
    # (~39 GiB, ~5 GiB margin) boots all three clean and verified v2 end-to-end.
    { name = "coder", repo = "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ", port = 8001, mem_util = 0.36, max_len = 8192, dtype = "", cost_in = 0.00000040, cost_out = 0.00000040 },
    # R1-Distill-QWEN-7B (not Llama): the Llama distill leaks BPE byte markers
    # (Ġ/Ċ) under vLLM 0.24's V1 detokenizer — reproduced across 2 quants, and
    # 0.24 is already the newest vLLM (no image bump available). The Qwen tokenizer
    # decodes clean (coder proves it). Trade: a 2nd Qwen member dents the lineage
    # decorrelation — acceptable for v2; restoring a Llama-lineage reasoner (V0
    # engine VLLM_USE_V1=0, or a future vLLM) is a v3 experiment. FP8-dynamic:
    # reputable RedHat quant, native on the L40S (Ada), ~8 GB, no AWQ-dtype hassle.
    { name = "reasoning", repo = "RedHatAI/DeepSeek-R1-Distill-Qwen-7B-FP8-dynamic", port = 8002, mem_util = 0.26, max_len = 8192, dtype = "", cost_in = 0.00000020, cost_out = 0.00000020 },
    { name = "general", repo = "hugging-quants/gemma-2-9b-it-AWQ-INT4", port = 8003, mem_util = 0.24, max_len = 8192, dtype = "", cost_in = 0.00000030, cost_out = 0.00000030 },
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
