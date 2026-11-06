# Two AZs. The tasks sit in public subnets with a public address and no inbound rule from the
# Internet, so they reach ECR, the LLM providers and Jev without a NAT gateway (about $0.059/hour
# plus $0.059/GB, more than half of what the demo pays otherwise). The database and Valkey sit in
# subnets with no route to the Internet at all. The trade-off is written up in
# docs/architecture/scalable-design.md, "Phạm vi triển khai trong đồ án".

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "microlearning-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
  cidr = "10.40.0.0/16"
}

resource "aws_vpc" "main" {
  cidr_block           = local.cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = length(local.azs)
  vpc_id                  = aws_vpc.main.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(local.cidr, 4, count.index)
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "data" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(local.cidr, 4, 8 + count.index)
  tags              = { Name = "${local.name}-data-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# No route out: the data subnets use the VPC's main route table, which only knows the VPC.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name}-data" }
}

resource "aws_route_table_association" "data" {
  count          = length(aws_subnet.data)
  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# Uploads and image layers from ECR go to S3 over this endpoint: free, and off the Internet.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
  tags              = { Name = "${local.name}-s3" }
}
