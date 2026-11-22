resource "aws_ecs_cluster" "main" {
  name = local.name

  # Off: about $0.10 per metric per month across every task, and the queue metrics the worker
  # writes plus the ALB and RDS metrics are what the scaling and the alarms use.
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

# Service Connect: api and worker reach the gateway as http://gateway:4000, the same name compose
# uses, so no URL differs between them.
resource "aws_service_discovery_http_namespace" "main" {
  name = local.name
}

locals {
  backend = "${aws_ecr_repository.repo["backend"].repository_url}:${var.image_tag}"

  # The client address uvicorn reports is the right-most one in X-Forwarded-For that is not a
  # trusted proxy. Trusting the VPC (the load balancer) and CloudFront's origin-facing ranges
  # leaves the address CloudFront saw, which a client cannot forge. Trusting "*" would hand
  # the rate limits a value the client writes itself.
  forwarded_allow_ips = join(",", concat([local.cidr], [for e in data.aws_ec2_managed_prefix_list.cloudfront.entries : e.cidr]))

  backend_environment = {
    ENVIRONMENT       = "production"
    LOG_FORMAT        = "json"
    AWS_REGION        = var.region
    RUN_JOB_WORKER    = "false"
    UPLOAD_STORE      = "s3"
    UPLOAD_S3_BUCKET  = aws_s3_bucket.uploads.bucket
    UPLOAD_S3_REGION  = var.region
    LLM_GATEWAY_URL   = "http://gateway:4000"
    LLM_MODEL         = var.llm_model
    MOODLE_BASE_URL   = var.moodle_base_url
    MOODLE_WWWROOT    = var.moodle_wwwroot
    WORKER_POOL_SIZE  = tostring(var.worker.pool_size)
    SELF_REGISTRATION = tostring(var.self_registration)
    # The LTI redirect URI is built from it, and must match what the LMS has registered.
    PUBLIC_URL = "https://${aws_cloudfront_distribution.main.domain_name}"
  }

  backend_secrets = {
    APP_RUNTIME_DATABASE_URL = local.ssm["app-runtime-database-url"]
    APP_WORKER_DATABASE_URL  = local.ssm["app-worker-database-url"]
    SECRET_KEY               = local.ssm["secret-key"]
    LLM_GATEWAY_KEY          = local.ssm["llm-gateway-key"]
    MOODLE_TOKEN             = local.ssm["moodle-token"]
    TYPESAFE_API_KEY         = local.ssm["typesafe-api-key"]
    LTI_PRIVATE_KEY          = local.ssm["lti-private-key"]
  }

  # The provider keys reach the gateway and nothing else: the backend never holds one.
  secrets_by_task = {
    api      = local.backend_secrets
    worker   = local.backend_secrets
    renderer = local.backend_secrets
    migrate = {
      DATABASE_URL             = local.ssm["database-url"]
      APP_RUNTIME_DATABASE_URL = local.ssm["app-runtime-database-url"]
      APP_WORKER_DATABASE_URL  = local.ssm["app-worker-database-url"]
    }
    gateway = {
      LLM_GATEWAY_KEY    = local.ssm["llm-gateway-key"]
      OPENROUTER_API_KEY = local.ssm["openrouter-api-key"]
    }
  }

  python_get = "python -c \"import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=4)\""

  tasks = {
    api = {
      image   = local.backend
      cpu     = var.api.cpu
      memory  = var.api.memory
      command = ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "25"]
      environment = merge(local.backend_environment, {
        RATE_LIMIT_STORAGE_URI = "rediss://${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
        FORWARDED_ALLOW_IPS    = local.forwarded_allow_ips
      })
      port         = 8000
      health       = "${local.python_get} http://127.0.0.1:8000/health"
      start_period = 30
      # Lets open chat streams end before the task is killed.
      stop_timeout = 30
      task_role    = aws_iam_role.task["api"].arn
    }
    worker = {
      image   = local.backend
      cpu     = var.worker.cpu
      memory  = var.worker.memory
      command = ["/app/.venv/bin/python", "-m", "app.jobs.worker"]
      environment = merge(local.backend_environment, {
        QUEUE_METRICS = "true"
        WORKER_QUEUE  = "general"
        # Below stop_timeout, so the worker puts unfinished jobs back itself before it is killed.
        WORKER_SHUTDOWN_GRACE_SECONDS = "100"
      })
      port         = null
      health       = null
      start_period = 0
      stop_timeout = 120 # the most Fargate allows
      task_role    = aws_iam_role.task["worker"].arn
    }
    # The worker's image, role and network, taking only video renders, one at a time.
    renderer = {
      image   = local.backend
      cpu     = var.renderer.cpu
      memory  = var.renderer.memory
      command = ["/app/.venv/bin/python", "-m", "app.jobs.worker"]
      environment = merge(local.backend_environment, {
        WORKER_QUEUE                  = "render"
        WORKER_POOL_SIZE              = "1"
        WORKER_SHUTDOWN_GRACE_SECONDS = "100"
      })
      port         = null
      health       = null
      start_period = 0
      stop_timeout = 120
      task_role    = aws_iam_role.task["worker"].arn
    }
    migrate = {
      image        = local.backend
      cpu          = 512
      memory       = 1024
      command      = ["/app/.venv/bin/alembic", "upgrade", "head"]
      environment  = { AWS_REGION = var.region }
      port         = null
      health       = null
      start_period = 0
      stop_timeout = 30
      task_role    = null
    }
    gateway = {
      image        = "${aws_ecr_repository.repo["gateway"].repository_url}:${var.image_tag}"
      cpu          = var.gateway.cpu
      memory       = var.gateway.memory
      command      = null
      environment  = {}
      port         = 4000
      health       = "${local.python_get} http://127.0.0.1:4000/health/liveliness"
      start_period = 60
      stop_timeout = 30
      task_role    = null
    }
  }

  enabled_tasks = local.tasks
}

resource "aws_cloudwatch_log_group" "task" {
  for_each          = local.enabled_tasks
  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "task" {
  for_each                 = local.enabled_tasks
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = each.value.task_role

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # the images are built on x86 machines
  }

  container_definitions = jsonencode([merge(
    {
      name         = each.key
      image        = each.value.image
      essential    = true
      stopTimeout  = each.value.stop_timeout
      environment  = [for k, v in each.value.environment : { name = k, value = v }]
      secrets      = [for k, arn in local.secrets_by_task[each.key] : { name = k, valueFrom = arn }]
      portMappings = each.value.port == null ? [] : [{ name = "http", containerPort = each.value.port, protocol = "tcp" }]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.task[each.key].name
          awslogs-region        = var.region
          awslogs-stream-prefix = each.key
          # A slow CloudWatch must not stall the process on a full pipe.
          mode            = "non-blocking"
          max-buffer-size = "25m"
        }
      }
    },
    each.value.command == null ? {} : { command = each.value.command },
    each.value.health == null ? {} : {
      healthCheck = {
        command     = ["CMD-SHELL", each.value.health]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = each.value.start_period
      }
    },
  )])
}

locals {
  # name => desired count at creation, and what it serves on Service Connect, if anything.
  services = merge(
    {
      api      = { count = var.api.min, security_group = aws_security_group.api.id, serves = null }
      worker   = { count = var.worker.min, security_group = aws_security_group.worker.id, serves = null }
      renderer = { count = var.renderer.min, security_group = aws_security_group.worker.id, serves = null }
      gateway  = { count = var.gateway.min, security_group = aws_security_group.gateway.id, serves = { name = "gateway", port = 4000 } }
    },
  )
}

resource "aws_ecs_service" "service" {
  for_each               = local.services
  name                   = each.key
  cluster                = aws_ecs_cluster.main.id
  task_definition        = aws_ecs_task_definition.task[each.key].arn
  desired_count          = each.value.count
  launch_type            = "FARGATE"
  enable_execute_command = var.enable_ecs_exec && contains(["api", "worker"], each.key)
  propagate_tags         = "SERVICE"

  # Time for a new api task to start before the load balancer's failed checks count against it.
  health_check_grace_period_seconds = each.key == "api" ? 60 : null

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [each.value.security_group]
    assign_public_ip = true # the way out without a NAT gateway, see network.tf
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.main.arn

    dynamic "service" {
      for_each = each.value.serves == null ? [] : [each.value.serves]
      content {
        port_name      = "http"
        discovery_name = service.value.name
        client_alias {
          dns_name = service.value.name
          port     = service.value.port
        }
      }
    }
  }

  dynamic "load_balancer" {
    for_each = each.key == "api" ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.api.arn
      container_name   = "api"
      container_port   = 8000
    }
  }

  lifecycle {
    # Autoscaling owns the count after creation.
    ignore_changes = [desired_count]
  }

  depends_on = [aws_lb_listener.http]
}
