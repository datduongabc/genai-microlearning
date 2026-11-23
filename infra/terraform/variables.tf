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
  description = "Tag of the images in ECR (backend, gateway). A commit hash: images are immutable."
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

variable "renderer" {
  # Video renders only (app/jobs/queue.py RENDER_JOB_TYPES), one per task: ffmpeg and Chromium hold
  # the CPU for minutes, which on a general worker slowed every job beside them, and two videos on
  # one vCPU each took twice as long. At zero when nothing renders; the general workers report the
  # renders waiting, which starts one task each.
  type    = object({ cpu = number, memory = number, min = number, max = number })
  default = { cpu = 2048, memory = 4096, min = 0, max = 4 }

  validation {
    condition     = var.renderer.min >= 0 && var.renderer.max >= max(var.renderer.min, 1)
    error_message = "renderer.max must be at least 1 and at least renderer.min."
  }
}

variable "gateway" {
  # Every model call goes through it, so one task is the one fault that stops all AI: two at least,
  # each in its own zone, and more by CPU when generation and chat are busy.
  type    = object({ cpu = number, memory = number, min = number, max = number })
  default = { cpu = 512, memory = 1024, min = 2, max = 4 }
}

variable "api_requests_per_target" {
  description = "Requests per minute per api task the scaling keeps to. Measured on AWS: one task saturates at about 127 requests/s and keeps p95 under 1 s at 86, so this (about 67) leaves room for the minutes a new task takes to start."
  type        = number
  default     = 4000
}

variable "self_registration" {
  description = "Whether anyone may create an account at /register. Off for a public address: the daily generation quota is per account, so open registration lets anyone create accounts to spend the provider credit. The administrator creates accounts, and an LMS launch creates them for its users."
  type        = bool
  default     = false
}

variable "llm_model" {
  description = "Gateway alias for chat and module generation; see devops/gateway/config.yaml. The same default as compose and backend/app/config.py."
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
  description = "Point-in-time restore window. Backup storage up to the database size is free, and one day leaves no restore point once a bad change is found the next morning."
  type        = number
  default     = 7
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
