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
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  # 100% actual → SNS → hard-stop lambda
  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.hardstop.arn]
  }
}

resource "aws_ce_anomaly_monitor" "services" {
  name              = "conclave-anomaly"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "email" {
  name             = "conclave-anomaly-email"
  frequency        = "DAILY"
  monitor_arn_list = [aws_ce_anomaly_monitor.services.arn]

  subscriber {
    type    = "EMAIL"
    address = var.alert_email
  }

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["10"]
    }
  }
}
