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

# Ungated on HF (no token needed). Llama 3.3 70B AWQ is the alternative but
# is gated — decide at launch per design doc.
variable "model_id" {
  type    = string
  default = "Qwen/Qwen2.5-72B-Instruct-AWQ"
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
