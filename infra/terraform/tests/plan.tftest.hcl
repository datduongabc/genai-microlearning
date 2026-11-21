# Plans against a mocked AWS: no account, no cost. It evaluates every expression, for_each and
# check the way a real plan does, so a wrong wiring shows here instead of halfway through an apply.
#   terraform init -backend=false && terraform test

mock_provider "aws" {
  source = "./tests/mocks/aws"
}

mock_provider "aws" {
  alias = "us_east_1"
}

variables {
  image_tag = "50d64a7"
}

# Known at plan time, so the CSP that names it can be checked.
override_resource {
  target          = aws_s3_bucket.uploads
  override_during = plan
  values          = { bucket_regional_domain_name = "up.s3.ap-southeast-1.amazonaws.com" }
}

run "demo_with_the_self_hosted_reranker" {
  command = plan

  variables {
    reranker_provider = "jina"
  }

  assert {
    condition     = toset(keys(aws_ecs_service.service)) == toset(["api", "worker", "renderer", "gateway", "embedder", "reranker"])
    error_message = "jina needs the reranker service."
  }
  assert {
    condition     = output.image_tag == "50d64a7"
    error_message = "infra/scripts/tf.sh reads the deployed tag back from this output."
  }
  assert {
    condition     = length(aws_subnet.public) == 2 && length(aws_subnet.data) == 2
    error_message = "Two AZs, even where the region has three."
  }
  assert {
    condition     = !contains(keys(aws_cloudwatch_metric_alarm.alarm), "jev-fallbacks")
    error_message = "No Jev alarm while Jev is not used."
  }
  assert {
    condition     = length(aws_cloudwatch_log_metric_filter.quality) == 2 * length(local.quality_metrics) && aws_cloudwatch_log_metric_filter.quality["ChatRefused-api"].metric_transformation[0].value == "$.refused"
    error_message = "Every quality field is counted from both the api and the worker logs."
  }
  assert {
    condition     = contains(keys(aws_cloudwatch_metric_alarm.alarm), "fact-check-failing")
    error_message = "A failing fact-check judge must raise an alarm: drafts would go out unchecked."
  }
  assert {
    condition     = length(jsondecode(aws_cloudwatch_dashboard.quality.dashboard_body).widgets) == 3
    error_message = "The AI quality dashboard has its three rates."
  }
  assert {
    # CloudWatch rejects the body unless every metric line is itself a list: ["namespace", "name", {...}].
    condition     = alltrue([for w in jsondecode(aws_cloudwatch_dashboard.quality.dashboard_body).widgets : alltrue([for m in w.properties.metrics : can(m[0])]) && length(w.properties.metrics) % 3 == 0])
    error_message = "A dashboard metric line is not a list."
  }
  assert {
    condition     = local.connections_peak == 4 * 22 + 8 * 32 + 4 * 11 + 5
    error_message = "Connection budget changed: ${local.connections_peak}."
  }
  assert {
    condition     = strcontains(local.tasks.api.environment.FORWARDED_ALLOW_IPS, "10.40.0.0/16") && strcontains(local.tasks.api.environment.FORWARDED_ALLOW_IPS, "13.32.0.0/15")
    error_message = "uvicorn must trust the load balancer and CloudFront, and nothing else."
  }
  assert {
    condition     = !contains(keys(local.secrets_by_task.api), "OPENROUTER_API_KEY") && contains(keys(local.secrets_by_task.gateway), "OPENROUTER_API_KEY")
    error_message = "Provider keys reach the gateway and nothing else."
  }
  assert {
    condition     = contains(keys(local.secrets_by_task.migrate), "DATABASE_URL") && !contains(keys(local.secrets_by_task.api), "DATABASE_URL")
    error_message = "Only the migration may hold the owner's URL."
  }
  assert {
    condition     = local.tasks.worker.environment.QUEUE_METRICS == "true" && !contains(keys(local.tasks.api.environment), "QUEUE_METRICS")
    error_message = "The workers report the queue depth."
  }
  assert {
    condition     = tonumber(local.tasks.worker.environment.WORKER_SHUTDOWN_GRACE_SECONDS) < local.tasks.worker.stop_timeout
    error_message = "The worker must release its jobs before ECS kills it."
  }
  assert {
    condition = (local.tasks.worker.environment.WORKER_QUEUE == "general" && local.tasks.renderer.environment.WORKER_QUEUE == "render"
    && local.tasks.renderer.environment.WORKER_POOL_SIZE == "1" && !contains(keys(local.tasks.renderer.environment), "QUEUE_METRICS"))
    error_message = "Renders run one per task on the renderer service, every other job on the workers, which report both queues."
  }
  assert {
    condition     = aws_ecs_service.service["renderer"].desired_count == 0 && aws_cloudwatch_metric_alarm.renderer_backlog.metric_name == "RenderJobsReady"
    error_message = "The renderer starts at zero and is started by the renders waiting."
  }
  assert {
    condition     = aws_lb_target_group.api.health_check[0].path == "/ready"
    error_message = "The load balancer checks /ready."
  }
  assert {
    condition     = local.tasks.api.environment.UPLOAD_S3_REGION == "ap-southeast-1"
    error_message = "A presigned POST must name the bucket's regional endpoint."
  }
  assert {
    condition     = local.tasks.api.environment.SELF_REGISTRATION == "false"
    error_message = "A public deployment must not let anyone register: the quota is per account."
  }
  assert {
    condition     = one(aws_s3_bucket_cors_configuration.uploads.cors_rule).allowed_methods == toset(["POST"])
    error_message = "The browser may only POST to the uploads bucket."
  }
}

run "jev_is_the_default" {
  command = plan

  assert {
    condition     = var.reranker_provider == "jev" && !contains(keys(aws_ecs_service.service), "reranker")
    error_message = "Jev is the default since 2026-11-08, so the default plan runs no reranker task."
  }
}

run "jev_drops_the_reranker_and_adds_its_alarms" {
  command = plan

  variables {
    reranker_provider = "jev"
  }

  assert {
    condition     = !contains(keys(aws_ecs_service.service), "reranker") && !contains(keys(aws_ecs_task_definition.task), "reranker")
    error_message = "No reranker task with Jev."
  }
  assert {
    condition     = contains(keys(aws_cloudwatch_metric_alarm.alarm), "jev-fallbacks") && contains(keys(aws_cloudwatch_metric_alarm.alarm), "jev-key-or-credit")
    error_message = "Jev falling back must raise an alarm."
  }
}

run "a_scale_the_database_cannot_hold_is_reported" {
  command = plan

  variables {
    worker = { cpu = 1024, memory = 3072, min = 1, max = 16, pool_size = 4 }
  }

  expect_failures = [check.connection_budget]
}

run "worker_cannot_scale_to_zero" {
  command = plan

  variables {
    worker = { cpu = 1024, memory = 3072, min = 0, max = 8, pool_size = 4 }
  }

  expect_failures = [var.worker]
}

run "the_page_may_talk_only_to_itself_and_the_uploads_bucket" {
  command = plan

  override_resource {
    target          = aws_cloudfront_response_headers_policy.security
    override_during = plan
    values          = { id = "security-headers" }
  }

  assert {
    condition     = strcontains(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "connect-src 'self' https://up.s3.ap-southeast-1.amazonaws.com;")
    error_message = "The browser posts uploads to the bucket and talks to nothing else: ${aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy}"
  }
  assert {
    condition     = strcontains(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "img-src 'self' data: blob:;") && strcontains(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "font-src 'self' https://fonts.gstatic.com data:;") && endswith(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "frame-ancestors 'none'")
    error_message = "No outside images, and no framing without an LMS: ${aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy}"
  }
  assert {
    condition     = strcontains(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "media-src 'self' blob: https://up.s3.ap-southeast-1.amazonaws.com;")
    error_message = "Videos play from a presigned bucket link, or from a blob without S3: ${aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy}"
  }
  assert {
    condition = alltrue([
      aws_cloudfront_distribution.main.default_cache_behavior[0].response_headers_policy_id == "security-headers",
      aws_cloudfront_distribution.main.ordered_cache_behavior[0].response_headers_policy_id == "security-headers",
    ])
    error_message = "Both behaviours carry the security headers."
  }
}

run "an_lms_may_frame_the_page" {
  command = plan

  variables {
    moodle_wwwroot = "https://moodle.example.edu/lms"
  }

  assert {
    condition     = endswith(aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy, "frame-ancestors https://moodle.example.edu")
    error_message = "frame-ancestors is the LMS origin: ${aws_cloudfront_response_headers_policy.security.security_headers_config[0].content_security_policy[0].content_security_policy}"
  }
}
