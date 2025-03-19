terraform {
  required_providers {
    kubernetes = {
      source = "hashicorp/kubernetes"
      version = "2.36.0"
    }

    vault = {
      source = "hashicorp/vault"
      version = "3.25.0"
    }
  }
}

provider "vault" {}
provider "kubernetes" {
  config_path = "~/.kube/config"
}
