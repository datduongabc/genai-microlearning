data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
    }
  }
}

# --- Execution role: what ECS itself needs to start a task. Each service reads only its own secrets.

resource "aws_iam_role" "execution" {
  for_each           = local.enabled_tasks
  name               = "${local.name}-${each.key}-exec"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  for_each   = aws_iam_role.execution
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  for_each = { for k in keys(local.enabled_tasks) : k => local.secrets_by_task[k] if length(local.secrets_by_task[k]) > 0 }
  name     = "read-own-secrets"
  role     = aws_iam_role.execution[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = distinct(values(each.value))
    }]
  })
}

# --- Task roles: what the code inside may do.

resource "aws_iam_role" "task" {
  for_each           = toset(["api", "worker"])
  name               = "${local.name}-${each.key}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "api_task" {
  # PutObject is what a presigned POST signed with this role may do; GetObject covers the HEAD
  # that checks the file's size when the upload is completed.
  statement {
    sid       = "Uploads"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/uploads/*"]
  }
  # A study's reference files (app/api/study_files.py): the same presigned POST and size check, a
  # presigned GET for the raters' downloads, and a delete when the admin removes one. Their own
  # prefix, which the lifecycle rule does not expire.
  statement {
    sid       = "StudyFiles"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/study-files/*"]
  }
  # Generated images, voice and videos (app/storage.py media_store): the API presigns the video for
  # playback and deletes a document's files with it.
  statement {
    sid       = "Media"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/media/*"]
  }
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    sid       = "Uploads"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/uploads/*"]
  }
  # The hourly clean-up removes a reference file whose upload was never completed.
  statement {
    sid       = "StudyFiles"
    actions   = ["s3:DeleteObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/study-files/*"]
  }
  # The worker writes the images, voice and video it generates, and reads them back to render.
  statement {
    sid       = "Media"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.uploads.arn}/media/*"]
  }
  # app/jobs/ecs_protection.py: a worker with a job running asks not to be scaled in.
  statement {
    sid       = "ScaleInProtection"
    actions   = ["ecs:UpdateTaskProtection", "ecs:GetTaskProtection"]
    resources = ["arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*"]
  }
}

data "aws_iam_policy_document" "ecs_exec" {
  statement {
    actions = [
      "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "api_task" {
  name   = "uploads"
  role   = aws_iam_role.task["api"].id
  policy = data.aws_iam_policy_document.api_task.json
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "uploads-and-protection"
  role   = aws_iam_role.task["worker"].id
  policy = data.aws_iam_policy_document.worker_task.json
}

resource "aws_iam_role_policy" "ecs_exec" {
  for_each = var.enable_ecs_exec ? aws_iam_role.task : {}
  name     = "ecs-exec"
  role     = each.value.id
  policy   = data.aws_iam_policy_document.ecs_exec.json
}
