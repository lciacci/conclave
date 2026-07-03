terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
  # ponytail: local state — solo project, one machine. Move to S3 backend if a
  # second machine or CI ever touches this.
}

provider "aws" {
  region  = var.region
  profile = var.profile

  default_tags {
    tags = {
      project = "conclave"
    }
  }
}
