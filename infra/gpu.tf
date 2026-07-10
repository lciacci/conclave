# v1 GPU instance: g6e.xlarge (1x L40S 48GB), 70B AWQ on vLLM.
# Zero ingress — reachability is Tailscale only (outbound WireGuard, no
# inbound rules needed). Gated behind var.enable_gpu until quota approves.

# Base DLAMI: NVIDIA driver + docker + nvidia-container-toolkit preinstalled.
data "aws_ssm_parameter" "dlami" {
  name = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
}

# Subnet in the chosen AZ (var.gpu_az) — spot capacity is per-AZ, so we don't
# pin the arbitrary ids[0] which landed in the dry us-east-1d.
data "aws_subnets" "gpu_az" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "availability-zone"
    values = [var.gpu_az]
  }
}

resource "aws_security_group" "gpu" {
  name   = "conclave-gpu"
  vpc_id = data.aws_vpc.default.id

  # No ingress blocks: zero public ports, by design. Do not add any.
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "gpu" {
  name = "conclave-gpu"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Session Manager shell access — no SSH keys, no port 22.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.gpu.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "gpu_runtime" {
  name = "conclave-gpu-runtime"
  role = aws_iam_role.gpu.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          "arn:aws:ssm:${var.region}:*:parameter${var.tailscale_key_param}",
          "arn:aws:ssm:${var.region}:*:parameter${var.hf_token_param}",
        ]
      },
      {
        # Publish the GPU-util idle-stop metric. PutMetricData has no
        # resource-level scoping; constrain to the Conclave namespace.
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = "Conclave" }
        }
      },
    ]
  })
}

resource "aws_iam_instance_profile" "gpu" {
  name = "conclave-gpu"
  role = aws_iam_role.gpu.name
}

resource "aws_instance" "gpu" {
  count = var.enable_gpu ? 1 : 0

  ami                    = data.aws_ssm_parameter.dlami.value
  instance_type          = var.gpu_instance_type
  subnet_id              = data.aws_subnets.gpu_az.ids[0]
  vpc_security_group_ids = [aws_security_group.gpu.id]
  iam_instance_profile   = aws_iam_instance_profile.gpu.name

  root_block_device {
    volume_size = 100
    volume_type = "gp3"
  }

  # DOES NOT bound InsufficientInstanceCapacity. Verified 2026-07-09: an apply
  # sat in "Still creating..." for 26 min with create=3m set. EC2 returns capacity
  # errors as HTTP 500, the AWS SDK's retryer treats 500 as retryable, and that
  # retry loop is not governed by this timeout — so a dry AZ stalls instead of
  # failing. Only the wait-for-running phase honours it. To sweep AZs you need an
  # EXTERNAL wall-clock guard plus `TF_LOG=DEBUG | grep InsufficientInstanceCapacity`
  # (the error never reaches normal output). Recipe in docs/HANDOFF.md.
  timeouts {
    create = "3m"
  }

  # ponytail: spot with default (terminate) interruption behavior — on reclaim,
  # re-apply to relaunch; EFS keeps the weights so it's cheap. Stop-not-terminate
  # would need a launch template; add only if reclaims get annoying.
  dynamic "instance_market_options" {
    for_each = var.use_spot ? [1] : []
    content {
      market_type = "spot"
    }
  }

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    efs_dns        = "${aws_efs_file_system.models.id}.efs.${var.region}.amazonaws.com"
    models_json    = jsonencode(var.models)
    ts_key_param   = var.tailscale_key_param
    hf_token_param = var.hf_token_param
    region         = var.region
  })

  tags = { Name = "conclave-gpu" }

  depends_on = [aws_efs_mount_target.models]
}

# Idle-stop, primary: GPU utilization. Inference work is on the GPU, not the CPU,
# so this is the true "no inference for N minutes" signal (user-data publishes
# Conclave/GPUUtil every minute). notBreaching on missing data so a mid-boot gap
# or a dead publisher can't false-stop; the CPU alarm below is the safety backstop.
resource "aws_cloudwatch_metric_alarm" "gpu_idle_stop" {
  count = var.enable_gpu ? 1 : 0

  alarm_name          = "conclave-idle-stop-gpu"
  alarm_description   = "Stop the GPU instance after ${var.dev_mode ? 90 : var.idle_minutes}m of GPU idle"
  namespace           = "Conclave"
  metric_name         = "GPUUtil"
  dimensions          = { InstanceId = aws_instance.gpu[0].id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = (var.dev_mode ? 90 : var.idle_minutes) / 5
  threshold           = 5
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:stop"]
}

# Idle-stop, backstop: native CPU metric — always present, no publisher to die.
# Catches "truly idle" even if the GPU publisher fails. Longer window so it never
# fires while the GPU alarm is the one doing the work.
resource "aws_cloudwatch_metric_alarm" "gpu_idle_stop_cpu_backstop" {
  count = var.enable_gpu ? 1 : 0

  alarm_name          = "conclave-idle-stop-gpu-cpu-backstop"
  alarm_description   = "Backstop: stop the GPU instance after ${var.dev_mode ? 90 : var.idle_minutes}m of CPU idle"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = aws_instance.gpu[0].id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = (var.dev_mode ? 90 : var.idle_minutes) / 5
  threshold           = 5
  comparison_operator = "LessThanThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:stop"]
}

output "gpu_instance_id" {
  value = var.enable_gpu ? aws_instance.gpu[0].id : null
}
