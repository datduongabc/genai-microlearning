# Plans against a mocked AWS: no account, no cost. It evaluates every expression, for_each and
# check the way a real plan does, so a wrong wiring shows here instead of halfway through an apply.
#   terraform init -backend=false && terraform test

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-southeast-1a", "ap-southeast-1b", "ap-southeast-1c"] }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      id      = "pl-3b927c52"
      entries = [{ cidr = "13.32.0.0/15", description = "" }, { cidr = "52.84.0.0/15", description = "" }]
    }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:ap-southeast-1:123456789012:task-definition/x:1" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/x" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::x", bucket = "x" }
  }
  mock_resource "aws_ssm_parameter" {
    defaults = { arn = "arn:aws:ssm:ap-southeast-1:123456789012:parameter/x" }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:distribution/x" }
  }
  mock_resource "aws_service_discovery_http_namespace" {
    defaults = { arn = "arn:aws:servicediscovery:ap-southeast-1:123456789012:namespace/ns-x" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-southeast-1:123456789012:targetgroup/x/1" }
  }
  mock_resource "aws_appautoscaling_policy" {
    defaults = { arn = "arn:aws:autoscaling:ap-southeast-1:123456789012:scalingPolicy:x" }
  }
  mock_resource "aws_cloudfront_function" {
    defaults = { arn = "arn:aws:cloudfront::123456789012:function/x" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:ap-southeast-1:123456789012:x" }
  }
}

mock_provider "aws" {
  alias = "us_east_1"
}

variables {
  image_tag = "50d64a7"
}

run "demo_with_the_self_hosted_reranker" {
  command = plan

  variables {
    reranker_provider = "jina"
  }

  assert {
    condition     = toset(keys(aws_ecs_service.service)) == toset(["api", "worker", "gateway", "embedder", "reranker"])
    error_message = "jina needs the reranker service."
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
    condition     = local.connections_peak == 4 * 22 + 8 * 32 + 5
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
    condition     = aws_lb_target_group.api.health_check[0].path == "/ready"
    error_message = "The load balancer checks /ready."
  }
  assert {
    condition     = local.tasks.api.environment.UPLOAD_S3_REGION == "ap-southeast-1"
    error_message = "A presigned POST must name the bucket's regional endpoint."
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
