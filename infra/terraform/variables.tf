variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "environment" {
  description = "Name used in every resource name: demo, loadtest."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,15}$", var.environment))
    error_message = "Lower case letters, digits and dashes, at most 16 characters."
  }
}

variable "image_tag" {
  description = "Tag of the images in ECR (backend, gateway, embedder, reranker). A commit hash: images are immutable."
  type        = string
}

# --- Sizes and scaling. Defaults are the demo configuration of docs/architecture/scalable-design.md.

variable "api" {
  type    = object({ cpu = number, memory = number, min = number, max = number })
  default = { cpu = 1024, memory = 2048, min = 1, max = 4 }
}

variable "worker" {
  description = "min must stay 1 or more: the workers report the queue depth that scales them, so at 0 nothing could scale them up again."
  type        = object({ cpu = number, memory = number, min = number, max = number, pool_size = number })
  default     = { cpu = 1024, memory = 3072, min = 1, max = 8, pool_size = 4 }

  validation {
    condition     = var.worker.min >= 1 && var.worker.max >= var.worker.min
    error_message = "worker.min must be at least 1 and no more than worker.max."
  }
}

variable "embedder" {
  type    = object({ cpu = number, memory = number, min = number, max = number })
  default = { cpu = 4096, memory = 8192, min = 1, max = 2 }
}

variable "gateway" {
  type    = object({ cpu = number, memory = number, count = number })
  default = { cpu = 512, memory = 1024, count = 1 }
}

variable "api_requests_per_target" {
  description = "Requests per minute per api task the scaling keeps to. Measured: about 199 requests/s on one task, so this is about half."
  type        = number
  default     = 6000
}

# --- Retrieval

variable "reranker_provider" {
  description = "jev (approved 2026-11-08) sends the question and candidate chunk text to TypeSafe in the US and runs no reranker task. jina runs the self-hosted reranker service instead."
  type        = string
  default     = "jev"

  validation {
    condition     = contains(["jina", "jev"], var.reranker_provider)
    error_message = "jina or jev."
  }
}

variable "reranker" {
  description = "The self-hosted reranker, used only when reranker_provider is jina."
  type        = object({ cpu = number, memory = number, count = number })
  default     = { cpu = 4096, memory = 8192, count = 1 }
}

variable "llm_model" {
  description = "Gateway alias for module generation; see devops/gateway/config.yaml."
  type        = string
  default     = "openai-fast"
}

# --- Data

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_max_connections" {
  description = "max_connections of db_instance_class with the default parameter group, used to check the connection budget. db.t4g.medium (4 GiB): about 410."
  type        = number
  default     = 410
}

variable "db_multi_az" {
  type    = bool
  default = false
}

variable "db_backup_retention_days" {
  type    = number
  default = 1
}

variable "db_deletion_protection" {
  description = "On for anything holding real data. Off lets a demo environment be destroyed in one command; a final snapshot is still taken."
  type        = bool
  default     = false
}

variable "valkey_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "upload_retention_days" {
  description = "Uploads are deleted once embedded; this removes the ones a failed or abandoned job left."
  type        = number
  default     = 7
}

# --- Moodle and operations

variable "moodle_base_url" {
  description = "Where the backend reaches Moodle. Moodle is not part of this stack."
  type        = string
  default     = ""
}

variable "moodle_wwwroot" {
  type    = string
  default = ""
}

variable "alarm_email" {
  description = "Receives alarms and budget warnings. Empty: alarms still exist but notify no one."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  type    = number
  default = 200
}

variable "log_retention_days" {
  type    = number
  default = 14
}

variable "enable_ecs_exec" {
  description = "Allows `aws ecs execute-command` into the backend tasks, instead of SSH."
  type        = bool
  default     = true
}
