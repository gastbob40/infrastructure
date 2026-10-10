# Chronodrive residential egress

The internal HTTP CONNECT proxy uses Tailscale userspace networking and the
Home Assistant exit node `100.102.81.97`. Only the Meal Planner API pods may
connect to port 1055. No public ingress or host routing changes are needed.

Vault `k8s/meal-planner/backend` must contain `CHRONODRIVE_TAILSCALE_AUTH_KEY`.
Use a non-ephemeral device. The enrollment key is separate from the Chronodrive
credentials and is only mounted into this proxy. `TS_AUTH_ONCE=true` and a PVC
preserve the node identity across restarts; do not delete the PVC during updates.
The local-path volume is tied to its Kubernetes node: losing that node's storage
requires recovery of the state or a new enrollment key.

After enrollment, disable key expiry for `meal-planner-chronodrive-proxy` in
Tailscale Machines (or enroll it with a suitable tag and access policy). Auth key
expiry does not disconnect an enrolled node, but node key expiry is independent.
The tailnet policy must allow this identity to use `autogroup:internet`, and
Home Assistant must advertise an approved exit node.

Readiness checks Tailscale initialization, not Chronodrive reachability. Home
Assistant and the residential internet connection must be online for API calls.
Deploy through the Meal Planner ArgoCD application after committing changes.
