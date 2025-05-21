terraform {
  required_providers {
    kubernetes = {
      source = "hashicorp/kubernetes"
      version = "2.37.1"
    }

    vault = {
      source = "hashicorp/vault"
      version = "4.8.0"
    }
  }
}

provider "vault" {}
provider "kubernetes" {
  config_path = "~/.kube/config"
}
