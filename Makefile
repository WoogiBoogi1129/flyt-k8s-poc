SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

.PHONY: help render render-managed validate validate-managed preflight namespace inputs build build-flyt control-plane gpu-cell gpu-cell-whole vms start-vms deploy install-basic-guest test evidence cleanup purge fetch-source

help:
	@printf '%s\n' \
	  'make render         Render environment-specific manifests' \
	  'make render-managed Render fail-closed whole-GPU managed manifests' \
	  'make validate       Run local static validation' \
	  'make validate-managed Validate managed manifests without changing the cluster' \
	  'make preflight      Run the non-mutating GPU/DRA safety gate' \
	  'make build          Deploy Flyt and PyTorch builders' \
	  'make build-flyt     Deploy only the Flyt builder for CUDA smoke tests' \
	  'make control-plane  Deploy MongoDB and Cluster Manager' \
	  'make gpu-cell       Deploy the DRA-backed MIG GPU Cell' \
	  'make gpu-cell-whole Deploy the approved whole-GPU PVC-backed Cell' \
	  'make vms            Create halted KubeVirt VM definitions' \
	  'make start-vms      Start both VMs' \
	  'make install-basic-guest Install Flyt/CUDA probes in VM A' \
	  'make deploy         Build and deploy all definitions (VMs remain halted)' \
	  'make test           Run CUDA smoke tests' \
	  'make evidence       Collect local, ignored evidence' \
	  'make cleanup        Halt VMs and remove the GPU Cell' \
	  'make purge          Delete only this namespace PoC (explicit confirmation)'

render:
	./scripts/render-manifests.sh

render-managed:
	./scripts/render-managed-overlay.sh

validate-managed: render-managed
	kubectl apply --dry-run=server -f deploy/rendered/managed/all.yaml >/dev/null
	@printf '%s\n' 'managed_server_dry_run=PASS'

validate:
	./scripts/static-checks.sh

preflight:
	./scripts/preflight.sh

namespace:
	@source ./scripts/lib.sh; \
	kubectl create namespace "$$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

inputs: namespace render
	./scripts/create-input-configmaps.sh

build: inputs
	kubectl apply -f deploy/rendered/00-quota.yaml
	kubectl apply -f deploy/rendered/05-config.yaml
	kubectl apply -f deploy/rendered/10-builder.yaml
	kubectl apply -f deploy/rendered/12-pytorch-builder.yaml

build-flyt: inputs
	kubectl apply -f deploy/rendered/00-quota.yaml
	kubectl apply -f deploy/rendered/05-config.yaml
	kubectl apply -f deploy/rendered/10-builder.yaml

control-plane: namespace render
	./scripts/ensure-secrets.sh
	kubectl apply -f deploy/rendered/15-mongodb.yaml
	kubectl apply -f deploy/rendered/18-cluster-manager.yaml

gpu-cell: preflight namespace render
	kubectl apply -f deploy/rendered/20-gpu-cell.yaml

gpu-cell-whole: preflight namespace render
	kubectl apply -f deploy/rendered/21-gpu-cell-whole-pvc.yaml

vms: namespace render
	kubectl apply -f deploy/rendered/30-vms.yaml

start-vms:
	@source ./scripts/lib.sh; \
	"$$VIRTCTL" start -n "$$NAMESPACE" "$$VM_A"; \
	"$$VIRTCTL" start -n "$$NAMESPACE" "$$VM_B"

install-basic-guest:
	./scripts/install-basic-guest.sh

deploy: build control-plane gpu-cell vms

test:
	./scripts/run-basic-tests.sh

evidence:
	./scripts/collect-evidence.sh

cleanup:
	./scripts/cleanup.sh

purge:
	@source ./scripts/lib.sh; \
	CONFIRM_PURGE="$$NAMESPACE/flyt-poc" ./scripts/cleanup.sh --purge

fetch-source:
	./scripts/fetch-flyt-source.sh
