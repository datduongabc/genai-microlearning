# One group per service, and every rule names the one group allowed in. Nothing but the load
# balancer accepts traffic from outside the VPC, and it only from CloudFront.

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Load balancer: HTTP from CloudFront only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "api tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "worker" {
  name        = "${local.name}-worker"
  description = "worker tasks: no inbound traffic"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "embedder" {
  name        = "${local.name}-embedder"
  description = "embedder tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "gateway" {
  name        = "${local.name}-gateway"
  description = "llm-gateway tasks"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "reranker" {
  name        = "${local.name}-reranker"
  description = "reranker tasks, when reranker_provider is jina"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "RDS PostgreSQL"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "valkey" {
  name        = "${local.name}-valkey"
  description = "ElastiCache Valkey"
  vpc_id      = aws_vpc.main.id
}

# Every task may call out: ECR, CloudWatch, SSM, the LLM providers and Jev are all on the Internet.
resource "aws_vpc_security_group_egress_rule" "all" {
  for_each = {
    alb      = aws_security_group.alb.id
    api      = aws_security_group.api.id
    worker   = aws_security_group.worker.id
    embedder = aws_security_group.embedder.id
    gateway  = aws_security_group.gateway.id
    reranker = aws_security_group.reranker.id
  }
  security_group_id = each.value
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

locals {
  # target => sources allowed in, and the port.
  ingress = {
    "api-from-alb"          = { target = aws_security_group.api.id, source = aws_security_group.alb.id, port = 8000 }
    "embedder-from-gateway" = { target = aws_security_group.embedder.id, source = aws_security_group.gateway.id, port = 80 }
    "gateway-from-api"      = { target = aws_security_group.gateway.id, source = aws_security_group.api.id, port = 4000 }
    "gateway-from-worker"   = { target = aws_security_group.gateway.id, source = aws_security_group.worker.id, port = 4000 }
    "reranker-from-api"     = { target = aws_security_group.reranker.id, source = aws_security_group.api.id, port = 80 }
    "reranker-from-worker"  = { target = aws_security_group.reranker.id, source = aws_security_group.worker.id, port = 80 }
    "db-from-api"           = { target = aws_security_group.db.id, source = aws_security_group.api.id, port = 5432 }
    "db-from-worker"        = { target = aws_security_group.db.id, source = aws_security_group.worker.id, port = 5432 }
    "valkey-from-api"       = { target = aws_security_group.valkey.id, source = aws_security_group.api.id, port = 6379 }
  }
}

resource "aws_vpc_security_group_ingress_rule" "service" {
  for_each                     = local.ingress
  security_group_id            = each.value.target
  referenced_security_group_id = each.value.source
  ip_protocol                  = "tcp"
  from_port                    = each.value.port
  to_port                      = each.value.port
  description                  = each.key
}
