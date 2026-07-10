#!/bin/bash
# Fast-fail GPU capacity sweep. EC2 InsufficientInstanceCapacity comes back as an
# HTTP 500 that the AWS SDK retries silently — `timeouts { create }` does NOT bound
# it, so a dry AZ STALLS (26 min once) instead of failing. This watches
# TF_LOG=DEBUG for the capacity error and kills on sight, then moves to the next AZ.
# Orphan-checks after every kill (a cancelled RunInstances returns "context
# canceled", so AWS may have launched a box you never saw). See docs/HANDOFF.md.
#
# Usage:  scripts/sweep-gpu-capacity.sh <instance_type> <az1> [az2 ...]
# e.g.    scripts/sweep-gpu-capacity.sh g6e.xlarge us-east-1c us-east-1a us-east-1d us-east-1b
# Falls back to g6e.2xlarge (same single L40S, mem_utils unchanged) if xlarge is dry.
set -u
ITYPE="${1:?instance type required, e.g. g6e.xlarge}"; shift
AZS="$*"
PROFILE=yeti-conclave
INFRA="$(cd "$(dirname "$0")/../infra" && pwd)"
cd "$INFRA" || exit 2

for az in $AZS; do
  LOG=/tmp/tf-${ITYPE}-${az}.log
  : > "$LOG"
  echo "=== $ITYPE @ $az ==="
  TF_LOG=DEBUG terraform apply -auto-approve \
    -var enable_gpu=true -var dev_mode=true -var use_spot=false \
    -var gpu_instance_type="$ITYPE" -var gpu_az="$az" > "$LOG" 2>&1 &
  TFPID=$!

  DRY=0; LAUNCHED=0
  for _ in $(seq 1 30); do          # up to ~90s of watching (3s * 30)
    kill -0 $TFPID 2>/dev/null || { echo "  tf exited early"; break; }
    if grep -qm1 InsufficientInstanceCapacity "$LOG"; then
      echo "  DRY ($az) — InsufficientInstanceCapacity"; DRY=1
      kill -TERM $TFPID 2>/dev/null; sleep 2; kill -9 $TFPID 2>/dev/null
      break
    fi
    if grep -qm1 'Still creating\|Creation complete\|aws_instance.gpu: Creating' "$LOG"; then
      echo "  LAUNCHING ($az) — RunInstances accepted, provisioning"; LAUNCHED=1
      break
    fi
    sleep 3
  done

  if [ "$DRY" = 1 ]; then
    echo "  orphan-check:"
    aws ec2 describe-instances --filters "Name=tag:project,Values=conclave" \
      "Name=instance-state-name,Values=pending,running" --profile $PROFILE \
      --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text
    continue
  fi

  if [ "$LAUNCHED" = 1 ]; then
    echo "  waiting on terraform to finish (this AZ has capacity)..."
    wait $TFPID; RC=$?
    echo "  terraform exit=$RC"
    if [ $RC -eq 0 ]; then
      echo "SUCCESS: $ITYPE @ $az"; terraform output 2>/dev/null; exit 0
    fi
    echo "  apply failed rc=$RC — tail:"; tail -25 "$LOG"; exit 3
  fi

  echo "  UNCLEAR after 90s — tail:"; tail -15 "$LOG"
  wait $TFPID; RC=$?
  echo "  terraform exit=$RC"
  [ $RC -eq 0 ] && { echo "SUCCESS(late): $ITYPE @ $az"; terraform output; exit 0; }
done

echo "ALL DRY for $ITYPE across: $AZS"
exit 1
