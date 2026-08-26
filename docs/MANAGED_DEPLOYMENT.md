# Managed Kubernetes deployment

The legacy `deploy/templates` path reproduces the original experiment. New
runtime work lives under `deploy/kustomize` and uses prebuilt images instead of
passing binaries through a builder PVC.

## Shared-cluster safety boundary

- Only namespaces whose name begins with `flyt-` and which carry
  `app.kubernetes.io/part-of=flyt` may be mutated by managed scripts.
- GPU selection is an explicit UUID allow-list.
- Rendering fails when an approved GPU, or one of its MIG children, is already
  represented by an allocated DRA claim.
- The checked-in GPU overlay is inert: its selector is `false` and replicas are
  zero until locally rendered.
- The managed apply script rejects cluster-scoped and non-Flyt objects.

## Workflow

1. Build and publish the `cluster-manager` and `gpu-cell` targets from
   `images/flyt/Containerfile`.
2. Put image digests and approved GPU UUIDs in ignored `config.env`.
3. Create a dedicated labeled namespace.
4. Run `make validate-managed`.
5. Create the two runtime Secrets with `scripts/create-managed-secrets.sh`.
6. Apply with `scripts/apply-managed.sh`.

The current phase deliberately does not create or modify KubeVirt VMs. Guest
session reconciliation must move from direct MongoDB writes to the managed
session API before VM automation is enabled.
