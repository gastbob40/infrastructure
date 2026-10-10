# Chronodrive and HelloFresh residential egress

The internal HTTP CONNECT proxy uses Tailscale userspace networking and the
Home Assistant exit node `100.102.81.97`. Only the Meal Planner API pods may connect to port 1055. No public ingress or host routing changes are needed.

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

Readiness checks Tailscale initialization, not upstream API reachability. Home
Assistant and the residential internet connection must be online for API calls.
Deploy through the Meal Planner ArgoCD application after committing changes.

The backend mounts `meal-planner-api/chronodrive.properties` as an additional
Quarkus configuration file. Chronodrive uses its two REST-client proxy settings;
HelloFresh uses `hellofresh.proxy-address` for its Java HTTP client, including
login, refresh, API calls and image downloads. Other clients retain their routes.
No additional Tailscale enrollment key or Vault secret is needed.

HelloFresh was validated from the cluster with a fresh session and Temurin
17.0.20.1: login, token refresh and read-only subscriptions succeeded. The old
Red Hat 17.0.10 image received a 403; the runtime is a possible factor, not a
proven sole cause. The backend Dockerfile pins the tested Temurin image digest.

Publish and roll out that backend version before syncing these configuration
changes: they remove the obsolete `FLARESOLVERR_URL` and the temporary network
permission for FlareSolverr to use the proxy. The shared FlareSolverr deployment
itself is retained for other consumers. Verify backend health after rollout;
do not use delivery-update endpoints as authentication smoke tests.

To restore direct HelloFresh access, set `hellofresh.proxy-address=none`.
For Chronodrive, set its two `proxy-address` properties to `none`. Commit and
sync ArgoCD. Kustomize config hashes trigger a backend rollout on changes.
