# Budget breach → SNS → Lambda stops every project=conclave instance.
# Stops, not terminates (delta from design doc): stopping ends GPU billing —
# 99% of spend — while keeping the instance recoverable; lingering EBS is ~$8/mo.

resource "aws_sns_topic" "hardstop" {
  name = "conclave-hardstop"
}

resource "aws_sns_topic_policy" "allow_budgets" {
  arn = aws_sns_topic.hardstop.arn
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "budgets.amazonaws.com" }
      Action    = "SNS:Publish"
      Resource  = aws_sns_topic.hardstop.arn
    }]
  })
}

data "archive_file" "hardstop" {
  type        = "zip"
  source_file = "${path.module}/lambda/hardstop.py"
  output_path = "${path.module}/lambda/hardstop.zip"
}

resource "aws_lambda_function" "hardstop" {
  function_name    = "conclave-hardstop"
  filename         = data.archive_file.hardstop.output_path
  source_code_hash = data.archive_file.hardstop.output_base64sha256
  handler          = "hardstop.handler"
  runtime          = "python3.12"
  timeout          = 60
  role             = aws_iam_role.hardstop.arn
}

resource "aws_sns_topic_subscription" "hardstop" {
  topic_arn = aws_sns_topic.hardstop.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.hardstop.arn
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.hardstop.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.hardstop.arn
}

resource "aws_iam_role" "hardstop" {
  name = "conclave-hardstop"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "hardstop" {
  name = "stop-tagged-instances"
  role = aws_iam_role.hardstop.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:StopInstances"]
        Resource = "*"
        Condition = {
          StringEquals = { "aws:ResourceTag/project" = "conclave" }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}
