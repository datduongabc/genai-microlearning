locals {
  scalable = {
    api      = var.api
    worker   = var.worker
    renderer = var.renderer
    gateway  = var.gateway
  }
}

resource "aws_appautoscaling_target" "service" {
  for_each           = local.scalable
  service_namespace  = "ecs"
  scalable_dimension = "ecs:service:DesiredCount"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.service[each.key].name}"
  min_capacity       = each.value.min
  max_capacity       = each.value.max

  lifecycle {
    # infra/scripts/stack-power.sh suspends scaling while the stack is paused; an apply then must
    # not resume it and start tasks against a stopped database.
    ignore_changes = [suspended_state]
  }
}

# --- api: requests per task, and CPU for the chat streams that are few requests but long ones.

resource "aws_appautoscaling_policy" "api_requests" {
  name               = "${local.name}-api-requests"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.service["api"].resource_id
  scalable_dimension = aws_appautoscaling_target.service["api"].scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = var.api_requests_per_target
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.main.arn_suffix}/${aws_lb_target_group.api.arn_suffix}"
    }
  }
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each           = toset(["api", "gateway"])
  name               = "${local.name}-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.service[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.service[each.key].scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

# --- worker: jobs that are due and that no worker has taken (app/jobs/metrics.py).
#
# Step scaling, not target tracking: the work per job ranges from a 2 s HTML page to a module
# generation of minutes, so "jobs per task" has no stable target. A backlog that lasts two
# minutes adds workers in proportion to it; an empty queue for fifteen removes one at a time.
# Scale-in only stops workers with no job running, see app/jobs/ecs_protection.py.

resource "aws_appautoscaling_policy" "worker_out" {
  name               = "${local.name}-worker-out"
  policy_type        = "StepScaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.service["worker"].resource_id
  scalable_dimension = aws_appautoscaling_target.service["worker"].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 120
    metric_aggregation_type = "Maximum"

    # Bounds are relative to the alarm threshold of 1 job ready.
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 8
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 8
      metric_interval_upper_bound = 32
      scaling_adjustment          = 2
    }
    step_adjustment {
      metric_interval_lower_bound = 32
      scaling_adjustment          = 4
    }
  }
}

resource "aws_appautoscaling_policy" "worker_in" {
  name               = "${local.name}-worker-in"
  policy_type        = "StepScaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.service["worker"].resource_id
  scalable_dimension = aws_appautoscaling_target.service["worker"].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = -1
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_backlog" {
  alarm_name          = "${local.name}-worker-backlog"
  alarm_description   = "Jobs are due and no worker has taken them: add workers."
  namespace           = "Microlearning/Jobs"
  metric_name         = "JobsReady"
  statistic           = "Maximum" # every worker reports the same count
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.worker_out.arn]
}

resource "aws_cloudwatch_metric_alarm" "worker_idle" {
  alarm_name          = "${local.name}-worker-idle"
  alarm_description   = "Nothing has waited for a worker in 15 minutes: remove one."
  namespace           = "Microlearning/Jobs"
  metric_name         = "JobsReady"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 15
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  # No data means no worker reported, which is not evidence of an empty queue.
  treat_missing_data = "missing"
  alarm_actions      = [aws_appautoscaling_policy.worker_in.arn]
}

# --- renderer: video renders that are due and that no renderer has taken, as the general workers
# report them (RenderJobsReady). One task renders one video, so a backlog of n adds n tasks at
# once, up to max; a minute is enough to see one, since a lecturer waits for this one.

resource "aws_appautoscaling_policy" "renderer_out" {
  name               = "${local.name}-renderer-out"
  policy_type        = "StepScaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.service["renderer"].resource_id
  scalable_dimension = aws_appautoscaling_target.service["renderer"].scalable_dimension

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 90
    metric_aggregation_type = "Maximum"

    # Bounds are relative to the alarm threshold of 1 render ready.
    step_adjustment {
      metric_interval_lower_bound = 0
      metric_interval_upper_bound = 1
      scaling_adjustment          = 1
    }
    step_adjustment {
      metric_interval_lower_bound = 1
      metric_interval_upper_bound = 3
      scaling_adjustment          = 2
    }
    step_adjustment {
      metric_interval_lower_bound = 3
      scaling_adjustment          = 4
    }
  }
}

resource "aws_appautoscaling_policy" "renderer_in" {
  name               = "${local.name}-renderer-in"
  policy_type        = "StepScaling"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.service["renderer"].resource_id
  scalable_dimension = aws_appautoscaling_target.service["renderer"].scalable_dimension

  step_scaling_policy_configuration {
    # All idle ones at once: a renderer with a render running is protected (app/jobs/ecs_protection.py).
    adjustment_type         = "ExactCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 0
      scaling_adjustment          = var.renderer.min
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "renderer_backlog" {
  alarm_name          = "${local.name}-renderer-backlog"
  alarm_description   = "A video render is due and no renderer has taken it: add renderers."
  namespace           = "Microlearning/Jobs"
  metric_name         = "RenderJobsReady"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_appautoscaling_policy.renderer_out.arn]
}

resource "aws_cloudwatch_metric_alarm" "renderer_idle" {
  alarm_name          = "${local.name}-renderer-idle"
  alarm_description   = "No render has waited in 10 minutes: back to the minimum."
  namespace           = "Microlearning/Jobs"
  metric_name         = "RenderJobsReady"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 10
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  # No data means no worker reported, which is not evidence of an empty queue.
  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_appautoscaling_policy.renderer_in.arn]
}
