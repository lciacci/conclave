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
  }))
  default = [
    # 14B not 32B: a 32B coder + 2 small models can't co-reside on one 48 GB L40S
    # (2026-07-07 — 32B+Gemma alone filled 42/44 GiB, no room for the 3rd). 14B
    # leaves headroom for all three. The 32B returns in v3 on its own GPU.
    { name = "coder", repo = "Qwen/Qwen2.5-Coder-14B-Instruct-AWQ", port = 8001, mem_util = 0.28, max_len = 8192, dtype = "" },
    # ⚠️ REASONING MEMBER UNRESOLVED — pending model decision (see design.md).
    # R1-Distill-Llama-8B has a vLLM-0.24 detokenizer bug: BPE byte markers
    # (Ġ/Ċ) leak into output. Reproduced across TWO independent quants (jakiAJK
    # AWQ + NeuralMagic w8a8) → it's the Llama-distill family + vLLM, not the
    # quant. coder (Qwen) + general (Gemma) decode clean. w8a8 loads cleanest
    # (no sparsity/dtype issues); util 0.26 for KV headroom. Output still garbled
    # until the model choice is settled (likely R1-Distill-Qwen-7B, or newer vLLM).
    { name = "reasoning", repo = "neuralmagic/DeepSeek-R1-Distill-Llama-8B-quantized.w8a8", port = 8002, mem_util = 0.26, max_len = 8192, dtype = "" },
    { name = "general", repo = "hugging-quants/gemma-2-9b-it-AWQ-INT4", port = 8003, mem_util = 0.18, max_len = 8192, dtype = "" },
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
