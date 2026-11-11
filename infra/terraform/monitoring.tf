resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# --- Jev falling back to the search order (app/retrieval/reranker.py). Retrieval quality drops
# with no error anywhere, so this is the only way anyone learns of it. Requests and jobs both
# rerank, so both log groups count.

locals {
  rerank_log_groups = { for k in ["api", "worker"] : k => aws_cloudwatch_log_group.task[k].name }
}

resource "aws_cloudwatch_log_metric_filter" "jev_fallback" {
  for_each       = local.rerank_log_groups
  name           = "jev-fallback"
  log_group_name = each.value
  pattern        = "{ $.message = \"jev_fallback*\" }"
  metric_transformation {
    name          = "JevFallbacks"
    namespace     = "Microlearning/Retrieval"
    value         = "1"
    default_value = "0"
  }
}

# 401, 402, 403: the key or the credit, which lasts until someone acts. Logged at ERROR.
resource "aws_cloudwatch_log_metric_filter" "jev_needs_attention" {
  for_each       = local.rerank_log_groups
  name           = "jev-needs-attention"
  log_group_name = each.value
  pattern        = "{ ($.message = \"jev_fallback*\") && ($.level = \"ERROR\") }"
  metric_transformation {
    name          = "JevNeedsAttention"
    namespace     = "Microlearning/Retrieval"
    value         = "1"
    default_value = "0"
  }
}

locals {
  alarms = merge(
    var.reranker_provider == "jev" ? {
      jev-fallbacks = {
        description = "Jev failed and retrieval fell back to the search order more than 10 times in 5 minutes."
        namespace   = "Microlearning/Retrieval", metric = "JevFallbacks", statistic = "Sum"
        period      = 300, periods = 1, threshold = 10, operator = "GreaterThanThreshold", dimensions = {}
      }
      jev-key-or-credit = {
        description = "Jev refused the key or has no credit (401/402/403): every rerank falls back until someone acts."
        namespace   = "Microlearning/Retrieval", metric = "JevNeedsAttention", statistic = "Sum"
        period      = 60, periods = 1, threshold = 0, operator = "GreaterThanThreshold", dimensions = {}
      }
    } : {},
    {
      job-waiting = {
        description = "A due job has waited over 15 minutes for a worker (scalable-design.md, section 10)."
        namespace   = "Microlearning/Jobs", metric = "OldestReadyJobAgeSeconds", statistic = "Maximum"
        period      = 60, periods = 3, threshold = 900, operator = "GreaterThanThreshold", dimensions = {}
      }
      api-5xx = {
        description = "The api answered 5xx more than 20 times in 5 minutes."
        namespace   = "AWS/ApplicationELB", metric = "HTTPCode_Target_5XX_Count", statistic = "Sum"
        period      = 300, periods = 1, threshold = 20, operator = "GreaterThanThreshold"
        dimensions  = { LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
      }
      api-unhealthy = {
        description = "An api task fails /ready."
        namespace   = "AWS/ApplicationELB", metric = "UnHealthyHostCount", statistic = "Maximum"
        period      = 60, periods = 3, threshold = 0, operator = "GreaterThanThreshold"
        dimensions  = { LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
      }
      db-cpu = {
        description = "RDS CPU above 80% for 10 minutes."
        namespace   = "AWS/RDS", metric = "CPUUtilization", statistic = "Average"
        period      = 300, periods = 2, threshold = 80, operator = "GreaterThanThreshold"
        dimensions  = { DBInstanceIdentifier = aws_db_instance.main.identifier }
      }
      db-storage = {
        description = "RDS has under 2 GB free; storage autoscaling may have hit its maximum."
        namespace   = "AWS/RDS", metric = "FreeStorageSpace", statistic = "Minimum"
        period      = 300, periods = 1, threshold = 2 * 1024 * 1024 * 1024, operator = "LessThanThreshold"
        dimensions  = { DBInstanceIdentifier = aws_db_instance.main.identifier }
      }
      db-connections = {
        description = "Database connections above 80% of max_connections."
        namespace   = "AWS/RDS", metric = "DatabaseConnections", statistic = "Maximum"
        period      = 60, periods = 3, threshold = floor(var.db_max_connections * 0.8), operator = "GreaterThanThreshold"
        dimensions  = { DBInstanceIdentifier = aws_db_instance.main.identifier }
      }
    },
  )
}

resource "aws_cloudwatch_metric_alarm" "alarm" {
  for_each            = local.alarms
  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.statistic
  period              = each.value.period
  evaluation_periods  = each.value.periods
  threshold           = each.value.threshold
  comparison_operator = each.value.operator
  dimensions          = each.value.dimensions
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]
}

# --- Budget: the $200 credit, warned at $50, $100 and $150 as scalable-design.md section 11 plans.

resource "aws_budgets_budget" "monthly" {
  name         = local.name
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Spend before credits: by default a budget counts what is left after them, which is $0 for as
  # long as the credit lasts, so none of the warnings below would be sent until it had run out.
  cost_types {
    include_credit = false
    include_refund = false
  }

  dynamic "notification" {
    for_each = var.alarm_email == "" ? [] : [25, 50, 75]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alarm_email]
    }
  }

  dynamic "notification" {
    for_each = var.alarm_email == "" ? [] : [100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "FORECASTED"
      subscriber_email_addresses = [var.alarm_email]
    }
  }
}
