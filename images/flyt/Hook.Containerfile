# Supply a digest-pinned Sidecar-shim image matching the installed KubeVirt release.
ARG KUBEVIRT_SHIM_IMAGE
FROM ${KUBEVIRT_SHIM_IMAGE} AS shim
FROM docker.io/library/ubuntu:22.04@sha256:3b06811b2afd352be909dd088a004166d665dc76d38b13eada33522a9d915c6f
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=shim /sidecar-shim /usr/bin/sidecar-shim
COPY runtime/shm/control/domain_hook.py /usr/bin/onDefineDomain
RUN chmod 0755 /usr/bin/onDefineDomain
ENTRYPOINT ["/usr/bin/sidecar-shim"]
