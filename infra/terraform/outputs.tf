output "region" {
  value = var.region
}

output "url" {
  description = "The application."
  value       = "https://${aws_cloudfront_distribution.main.domain_name}"
}

# The tag the services run. infra/scripts/tf.sh passes it back to every plan and apply, so an infra
# change from any checkout keeps the images the last deploy rolled out.
output "image_tag" {
  value = var.image_tag
}

output "cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecr" {
  value = { for k, r in aws_ecr_repository.repo : k => r.repository_url }
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.main.id
}

output "ssm_prefix" {
  description = "Where the provider keys go: aws ssm put-parameter --overwrite --type SecureString --name <prefix>/openrouter-api-key (and gemini-api-key) --value ..."
  value       = local.ssm_prefix
}

# What scripts/deploy.sh needs to run the migration as a one-off task.
output "migrate" {
  value = {
    task_definition = aws_ecs_task_definition.task["migrate"].family
    subnets         = aws_subnet.public[*].id
    security_group  = aws_security_group.worker.id
  }
}

output "services" {
  value = keys(aws_ecs_service.service)
}

# What infra/scripts/stack-power.sh pauses and starts again: the count each service starts with
# (a service with a scalable target is given that target's minimum instead), and the database.
output "service_counts" {
  value = { for name, service in local.services : name => service.count }
}

output "db_instance" {
  value = aws_db_instance.main.identifier
}

output "connections_at_full_scale" {
  value = local.connections_peak
}

output "lti" {
  description = "What to enter in the LMS when registering the tool."
  value = {
    initiate_login_url = "https://${aws_cloudfront_distribution.main.domain_name}/api/lti/login"
    redirection_uri    = "https://${aws_cloudfront_distribution.main.domain_name}/api/lti/launch"
    public_keyset_url  = "https://${aws_cloudfront_distribution.main.domain_name}/api/lti/jwks"
  }
}
