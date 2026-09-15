.DEFAULT_GOAL := help
.PHONY: help build-shm render
help:
	@echo 'SHM-only source; validation NOT_RUN. See README.md before building/deploying.'
	@echo 'make build-shm    Configure/build the SHM runtime locally (explicit action)'
	@echo 'make render ARGS="..."    Emit SHM control-plane manifests; never apply'
build-shm:
	cmake -S runtime/shm -B .local/shm-build -DCMAKE_BUILD_TYPE=Release
	cmake --build .local/shm-build --parallel 2
render:
	python3 scripts/render-shm.py $(ARGS)
