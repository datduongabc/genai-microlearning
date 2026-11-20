# The production sizes of infra/environments/production.tfvars, planned against the mocked AWS. Run with
#   terraform test -filter=tests/production.tftest.hcl -var-file=../environments/production.tfvars

mock_provider "aws" {
  source = "./tests/mocks/aws"
}

mock_provider "aws" {
  alias = "us_east_1"
}

variables {
  image_tag = "50d64a7"
}

run "production_survives_a_zone_and_keeps_its_data" {
  command = plan

  assert {
    condition     = var.environment == "production" && aws_ecs_cluster.main.name == "microlearning-production"
    error_message = "Run with -var-file=../environments/production.tfvars."
  }
  assert {
    condition     = aws_db_instance.main.multi_az && aws_db_instance.main.deletion_protection
    error_message = "Production data survives a zone failing and a stray destroy."
  }
  assert {
    condition     = aws_appautoscaling_target.service["gateway"].min_capacity >= 2
    error_message = "One gateway task would stop every model call when it fails."
  }
  assert {
    condition     = local.connections_peak <= var.db_max_connections - 3
    error_message = "Production at full scale opens more connections than its database accepts."
  }
}
