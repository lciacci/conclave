# Monthly cap with tiered email alerts + SNS trigger for the hard stop.
# Note: AWS Budgets evaluates ~3x/day, not real-time. The hard stop bounds
# damage to a few hours of overrun, not zero. Idle-stop is the first line.

resource "aws_budgets_budget" "monthly" {
  name         = "conclave-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_cap_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  # 100% actual → email + SNS → hard-stop lambda. One block: Budgets keys
  # notifications on (type, operator, threshold) — two blocks at 100% collide.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [aws_sns_topic.hardstop.arn]
  }
}

# Anomaly detection: intentionally absent. New accounts ship with an
# auto-created Default-Services-Monitor + daily email subscription to the
# account address, and the account limit is 1 dimensional monitor. Ours would
# have been a duplicate that can't even be created.
