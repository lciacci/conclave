# Idle stop: per-instance CloudWatch alarm with a native EC2 stop action — no
# Lambda needed. v0.5 tests this on a t3.micro via CPUUtilization.
# ponytail: CPU < 5% is a crude idle proxy; v2 swaps to a LiteLLM
# request-count custom metric once real inference traffic exists to tune against.

resource "aws_cloudwatch_metric_alarm" "idle_stop" {
  for_each = toset(var.watched_instances)

  alarm_name          = "conclave-idle-stop-${each.value}"
  alarm_description   = "Stop ${each.value} after ${var.idle_minutes}m idle"
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  dimensions          = { InstanceId = each.value }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = var.idle_minutes / 5
  threshold           = 5
  comparison_operator = "LessThanThreshold"
  alarm_actions       = ["arn:aws:automate:${var.region}:ec2:stop"]
}
