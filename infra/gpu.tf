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

resource "aws_iam_role_policy" "read_ts_key" {
  name = "read-tailscale-authkey"
  role = aws_iam_role.gpu.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["ssm:GetParameter"]
      Resource = [
        "arn:aws:ssm:${var.region}:*:parameter${var.tailscale_key_param}",
        "arn:aws:ssm:${var.region}:*:parameter${var.hf_token_param}",
      ]
    }]
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
    model_id       = var.model_id
    ts_key_param   = var.tailscale_key_param
    hf_token_param = var.hf_token_param
    region         = var.region
  })

  tags = { Name = "conclave-gpu" }

  depends_on = [aws_efs_mount_target.models]
}

# Idle-stop rides the same alarm pattern as watched_instances.
resource "aws_cloudwatch_metric_alarm" "gpu_idle_stop" {
  count = var.enable_gpu ? 1 : 0

  alarm_name          = "conclave-idle-stop-gpu"
  alarm_description   = "Stop the GPU instance after ${var.idle_minutes}m idle"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = aws_instance.gpu[0].id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = var.idle_minutes / 5
  threshold           = 5
  comparison_operator = "LessThanThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:stop"]
}

output "gpu_instance_id" {
  value = var.enable_gpu ? aws_instance.gpu[0].id : null
}
