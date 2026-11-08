# Parameter Store SecureString, encrypted with the account's aws/ssm key. ECS reads them into the
# environment at task start; nothing is baked into an image or a task definition.
#
# Generated here: database URLs and the keys the services share. Placeholders: the provider keys,
# which Terraform must never see. Set them after the first apply (README), and Terraform leaves
# them alone afterwards.

resource "random_password" "secret_key" {
  length  = 64
  special = false
}

resource "random_password" "gateway_key" {
  length  = 40
  special = false
}

# The tool's LTI key: the api publishes its public half at /api/lti/jwks, the worker signs with it to
# read each course's member list from the LMS (backend/app/lti/roster.py). Replacing it gives it a new
# key id, so an LMS that cached the old key fetches the set again.
resource "tls_private_key" "lti" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "random_password" "embedder_key" {
  length  = 40
  special = false
}

locals {
  ssm_prefix = "/${local.name}"

  generated_parameters = {
    "database-url"             = local.db_url.microlearning_owner # migrations only
    "app-runtime-database-url" = local.db_url.app_runtime
    "app-worker-database-url"  = local.db_url.app_worker
    "secret-key"               = random_password.secret_key.result
    "llm-gateway-key"          = "sk-${random_password.gateway_key.result}"
    "embedder-api-key"         = random_password.embedder_key.result
    "lti-private-key"          = tls_private_key.lti.private_key_pem
  }

  external_parameters = [
    "gemini-api-key", "openrouter-api-key", "groq-api-key", "typesafe-api-key", "moodle-token",
  ]
}

resource "aws_ssm_parameter" "generated" {
  for_each = local.generated_parameters
  name     = "${local.ssm_prefix}/${each.key}"
  type     = "SecureString"
  value    = each.value
}

resource "aws_ssm_parameter" "external" {
  for_each = toset(local.external_parameters)
  name     = "${local.ssm_prefix}/${each.key}"
  type     = "SecureString"
  # An empty key makes that provider unusable, which is what the gateway expects of a missing one.
  value = "unset"

  lifecycle {
    ignore_changes = [value]
  }
}

locals {
  ssm = merge(
    { for k, p in aws_ssm_parameter.generated : k => p.arn },
    { for k, p in aws_ssm_parameter.external : k => p.arn },
  )
}
