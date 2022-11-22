resource "vault_mount" "this" {
  path = "k8s"
  type = "kv"
  options = {
    version = 2
  }
}

resource "vault_auth_backend" "this" {
  type = "approle"
  path = "k8s"
}

resource "vault_policy" "this" {
  name = "vault-secrets-operator"
  policy = <<-EOP
    path "${vault_auth_backend.this.path}/*" {
      capabilities = ["read"]
    }
  EOP
}

resource "vault_approle_auth_backend_role" "this" {
  backend = vault_auth_backend.this.path
  role_name = "vault-secrets-operator"
  token_policies = [vault_policy.this.name]
  token_ttl = 24 * 60 * 60 # 24h
}

resource "vault_approle_auth_backend_role_secret_id" "this" {
  backend = vault_auth_backend.this.path
  role_name = vault_approle_auth_backend_role.this.role_name
}

resource "kubernetes_secret" "this" {
  metadata {
    name = "vault-approle"
    namespace = "vault-secrets-operator"
  }

  data = {
    VAULT_ROLE_ID = vault_approle_auth_backend_role.this.role_id
    VAULT_SECRET_ID = vault_approle_auth_backend_role_secret_id.this.secret_id
    VAULT_TOKEN_MAX_TTL = vault_approle_auth_backend_role.this.token_ttl
  }

  type = "Opaque"
}
