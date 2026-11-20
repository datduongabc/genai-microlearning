# --- PostgreSQL with pgvector

resource "random_password" "db_owner" {
  length  = 32
  special = false # goes into a URL
}

resource "random_password" "db_runtime" {
  length  = 32
  special = false
}

resource "random_password" "db_worker" {
  length  = 32
  special = false
}

# A new suffix for every database, so destroying one never collides with the final snapshot an
# earlier destroy left, which a fixed name did.
resource "random_id" "final_snapshot" {
  byte_length = 4
}

resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_db_parameter_group" "main" {
  name   = local.name
  family = "postgres16"

  # A static parameter: RDS stores it as pending-reboot whatever is asked, and leaving the method
  # to its default made every plan show it as changed. A new instance starts with it in force.
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }
  # Logs a statement that runs over a second; the slow ones are what to look at first under load.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
}

resource "aws_db_instance" "main" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = 20
  max_allocated_storage = 100
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "microlearning"
  username = "microlearning_owner"
  password = random_password.db_owner.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.main.name
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period    = var.db_backup_retention_days
  deletion_protection        = var.db_deletion_protection
  skip_final_snapshot        = false
  final_snapshot_identifier  = "${local.name}-final-${random_id.final_snapshot.hex}"
  auto_minor_version_upgrade = true
  apply_immediately          = true
}

locals {
  db_host = aws_db_instance.main.address
  db_url = {
    for role, password in {
      microlearning_owner = random_password.db_owner.result
      app_runtime         = random_password.db_runtime.result
      app_worker          = random_password.db_worker.result
    } : role => "postgresql+psycopg://${role}:${password}@${local.db_host}:5432/microlearning?sslmode=require"
  }
}

# --- Valkey: rate limit counters only, which may be lost, so one node.

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.data[*].id
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id       = local.name
  description                = "Rate limit counters shared by the api tasks"
  engine                     = "valkey"
  engine_version             = "8.0"
  node_type                  = var.valkey_node_type
  num_cache_clusters         = 1
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.valkey.id]
  transit_encryption_enabled = true
  at_rest_encryption_enabled = true
  apply_immediately          = true
}

# --- Uploads, between the browser that sends them and the worker that reads them. The browser
# posts the file here with a presigned POST from the api task (app/storage.py); it never passes
# through the load balancer or an api task.

resource "aws_s3_bucket" "uploads" {
  bucket_prefix = "${local.name}-uploads-"
  force_destroy = true # nothing here outlives its job
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  # Course files are kept until their document is deleted (a study copies them as reference
  # files); abandoned uploads are removed by the worker's hourly clean-up, not by age.
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {
      prefix = "uploads/"
    }
    # A 1 GB video goes up in parts; an api task stopped half way leaves them billed.
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

# The presigned POST is a cross-origin request from the page CloudFront serves.
resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  cors_rule {
    allowed_methods = ["POST"]
    allowed_origins = ["https://${aws_cloudfront_distribution.main.domain_name}"]
    allowed_headers = ["*"]
    max_age_seconds = 3600
  }
}

resource "aws_s3_bucket_policy" "uploads_tls_only" {
  bucket = aws_s3_bucket.uploads.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.uploads.arn, "${aws_s3_bucket.uploads.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
