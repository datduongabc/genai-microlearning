output "region" {
  value = var.region
}

output "url" {
  description = "The application."
  value       = "https://${aws_cloudfront_distribution.main.domain_name}"
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
  description = "Where the provider keys go: aws ssm put-parameter --overwrite --type SecureString --name <prefix>/openai-api-key --value ..."
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

output "connections_at_full_scale" {
  value = local.connections_peak
}
