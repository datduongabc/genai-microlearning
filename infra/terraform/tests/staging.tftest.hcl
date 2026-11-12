# The staging sizes of infra/environments/staging.tfvars, planned against the mocked AWS. Run with
# that file, which the other test file must not get (it pins the demo sizes):
#   terraform test -filter=tests/staging.tftest.hcl -var-file=../environments/staging.tfvars

mock_provider "aws" {
  source = "./tests/mocks/aws"
}

mock_provider "aws" {
  alias = "us_east_1"
}

variables {
  image_tag = "50d64a7"
}

run "staging_is_its_own_environment" {
  command = plan

  assert {
    condition     = var.environment == "staging" && aws_ecs_cluster.main.name == "microlearning-staging"
    error_message = "Run with -var-file=../environments/staging.tfvars: every name must carry staging, or it collides with demo."
  }
  assert {
    condition     = toset(keys(aws_ecs_service.service)) == toset(["api", "worker", "gateway", "embedder"])
    error_message = "Staging runs every service demo runs with Jev."
  }
  assert {
    condition     = local.connections_peak <= var.db_max_connections - 3
    error_message = "Staging at full scale opens more connections than its database accepts."
  }
  assert {
    condition     = aws_budgets_budget.monthly.limit_amount == "30"
    error_message = "Staging has its own small budget."
  }
}
