# Connections every task can open at its maximum count, from the pool sizes in backend/app/db.py:
#   api: api_max_concurrent_requests (20) + 1 for /ready, and 1 spare
#   worker: (2 + question_concurrency) * pool_size + 2, plus max_overflow 2; a renderer is one with pool_size 1
# Above max_connections, scaling out would fail new connections exactly under load. PgBouncer in
# transaction mode is the next step then (scalable-design.md, section 7).
locals {
  connections_api    = 22
  connections_worker = (2 + 5) * var.worker.pool_size + 4
  connections_render = (2 + 5) * 1 + 4
  connections_peak = (var.api.max * local.connections_api + var.worker.max * local.connections_worker
  + var.renderer.max * local.connections_render + 5)
}

check "connection_budget" {
  assert {
    # RDS keeps 3 connections for its own use.
    condition     = local.connections_peak <= var.db_max_connections - 3
    error_message = "At full scale the tasks may open ${local.connections_peak} connections, above the ${var.db_max_connections - 3} the database accepts. Lower api.max, worker.max or renderer.max, or use a larger db_instance_class."
  }
}
