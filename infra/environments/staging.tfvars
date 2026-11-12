# Staging: the same stack as demo at the smallest sizes that still run every service, where a commit
# is deployed and checked before it is promoted to demo (infra/README.md, "Staging"). Applied with
#   ENVIRONMENT=staging infra/scripts/tf.sh apply
# terraform.tfvars (image_tag, alarm_email) is read too; values here take precedence.

environment = "staging"

api      = { cpu = 512, memory = 1024, min = 1, max = 2 }
worker   = { cpu = 1024, memory = 3072, min = 1, max = 2, pool_size = 4 }
embedder = { cpu = 4096, memory = 8192, min = 1, max = 1 } # the model needs this memory to load
gateway  = { cpu = 256, memory = 512, count = 1 }

# db.t4g.small (2 GiB): max_connections about 190 with the default parameter group, above the
# 113 that two api and two worker tasks can open (checks.tf).
db_instance_class  = "db.t4g.small"
db_max_connections = 190

upload_retention_days = 1
log_retention_days    = 3
# Its own budget, so a staging stack left running warns long before it eats the demo's credit.
monthly_budget_usd = 30
