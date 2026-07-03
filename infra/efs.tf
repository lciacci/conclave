# EFS = active model cache (design doc: EFS active / S3 cold / EBS OS-only).
# Costs nothing while empty; survives instance teardown.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_efs_file_system" "models" {
  creation_token = "conclave-models"
  # ponytail: bursting throughput — model reads are sequential and infrequent
  # (cold start only). Elastic throughput if cold loads ever hurt.
}

resource "aws_security_group" "efs" {
  name   = "conclave-efs"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 2049
    to_port         = 2049
    protocol        = "tcp"
    security_groups = [aws_security_group.gpu.id]
  }
}

resource "aws_efs_mount_target" "models" {
  for_each        = toset(data.aws_subnets.default.ids)
  file_system_id  = aws_efs_file_system.models.id
  subnet_id       = each.value
  security_groups = [aws_security_group.efs.id]
}
