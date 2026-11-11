terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # State holds the generated database passwords, so it must not sit on a laptop or in git. The
  # bucket and the key come from backend.hcl (ignored by git, see backend.hcl.example): the bucket
  # is named after the account, and each environment keeps its own key. use_lockfile locks with
  # an object next to the state, so no DynamoDB table is needed.
  backend "s3" {
    region       = "ap-southeast-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "microlearning"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# CloudFront, its certificate and the budget live in us-east-1 whatever the region.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "microlearning"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
