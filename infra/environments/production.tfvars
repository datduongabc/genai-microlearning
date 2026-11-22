# Production: the stack sized for about 30,000 users (10-20% online at once, most of them learners
# who chat), with the data protected as real data is. Applied with
#   ENVIRONMENT=production infra/scripts/tf.sh apply
# Not the demo: the demo keeps the smaller, single-zone defaults so it fits the $200 credit.

environment = "production"

api    = { cpu = 1024, memory = 2048, min = 2, max = 12 }
worker = { cpu = 1024, memory = 3072, min = 2, max = 12, pool_size = 4 }
# One render a task, started when a render waits: eight lecturers' videos at once.
renderer = { cpu = 2048, memory = 4096, min = 0, max = 8 }
gateway  = { cpu = 1024, memory = 2048, min = 2, max = 6 }

# A zone failing must not stop the system, and the database must not go with one command.
db_multi_az              = true
db_deletion_protection   = true
db_backup_retention_days = 14
# db.m7g.large (8 GiB): max_connections about 900 with the default parameter group, above the 741
# that 12 api, 12 worker and 8 renderer tasks can open (checks.tf). Past that, PgBouncer (scalable-design.md).
db_instance_class  = "db.m7g.large"
db_max_connections = 900
valkey_node_type   = "cache.t4g.small"

log_retention_days = 30
monthly_budget_usd = 1500
