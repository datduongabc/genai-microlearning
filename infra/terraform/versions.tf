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

  # State holds the generated database passwords, so it must not sit on a laptop or in git.
  # Create the bucket once (see README), then uncomment. use_lockfile needs no DynamoDB table.
  # backend "s3" {
  #   bucket       = "<account>-microlearning-tfstate"
  #   key          = "demo/terraform.tfstate"
  #   region       = "ap-southeast-1"
  #   encrypt      = true
  #   use_lockfile = true
  # }
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
