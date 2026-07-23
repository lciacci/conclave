# T1–T3 hosted-80B agentic-fidelity run (2026-07-22).
#
# ONE model, not a fleet. The experiment holds the aider harness and the T1–T3
# tasks fixed and changes exactly one variable: the model behind the endpoint
# (local qwen3-coder:30b Q4 -> this). Question: is T3's confabulated-completion
# failure a size/quant artifact, or a property of open-weight models in agentic
# harnesses?
#
# Kept in a tfvars file rather than editing the `models` default in variables.tf
# — that default carries the measured co-residency notes for the old 3-model
# L40S fleet and is worth not clobbering for a one-off run.
#
# Apply:  terraform apply -var-file=coder80.tfvars
# Teardown: terraform apply   (enable_gpu defaults false -> destroys the instance)

enable_gpu = true
# g6e.12xlarge (4x L40S, 48 vCPU) was DRY in 10/10 AZ-attempts across us-east-1 +
# us-east-2, spot and on-demand, 2026-07-22. Fell back to g6e.24xlarge: SAME four
# L40S, just 96 vCPU / bigger box — a DIFFERENT instance-type capacity pool, and
# 96 vCPU exactly fits us-east-2's 96 on-demand G quota (spot quota 64 and
# us-east-1 quota 64 are both too small, so this is us-east-2 on-demand ONLY).
gpu_instance_type = "g6e.24xlarge"
use_spot          = false        # 96 vCPU > us-east-2's 64 spot quota; on-demand only
gpu_az            = "us-east-2b" # overridden per-AZ by the capacity sweep
dev_mode          = true         # 90m idle window, not 30m — the 30m default reaped a box mid-work
ttl_minutes       = 120          # ~$30 ceiling at $15.07/hr — under the $35 envelope

# mem_util 0.85 PER CARD (not additive here — this is the only process on the box,
# so the free-check that forced additive slices for the co-resident fleet doesn't
# apply). Weights 74.9 GB / tp 4 = ~18.7 GB per 45.7 GB card, rest is KV.
#
# max_len 16384 matches the RunPod specialist-fleet run this is compared against.
#
# RISK, unresolved before boot: the FP8 blockwise quant wants compute capability
# >8.9 and L40S is exactly 8.9 (Ada). If ">" is literal this fails on load, ~15
# min and ~$3 in. Agreed disposition: ABORT and report the negative — do NOT
# swap to a 4-bit quant, which would confound size with quantization and destroy
# the experiment's only controlled variable.
models = [
  {
    name       = "coder80"
    repo       = "Qwen/Qwen3-Coder-Next-FP8"
    port       = 8001
    mem_util   = 0.85
    max_len    = 16384
    dtype      = ""
    tp         = 4
    extra_args = ""
    cost_in    = 0.00000040
    cost_out   = 0.00000040
  },
]
