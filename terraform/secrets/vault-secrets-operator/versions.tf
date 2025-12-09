terraform {
  required_providers {
    kubernetes = {
      source = "hashicorp/kubernetes"
      version = "3.0.1"
    }

    vault = {
      source = "hashicorp/vault"
      version = "5.5.0"
    }
  }
}

provider "vault" {}
provider "kubernetes" {
  config_path = "~/.kube/config"
}
