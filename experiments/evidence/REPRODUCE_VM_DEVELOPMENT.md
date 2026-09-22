# VM 개발 검증 재현

이 절차는 SHM GPU 복사·PTX·메모리·회수 개발 검증이다. PyTorch 학습 및
Passthrough/RPC·MPS 성능 비교 절차를 대신하지 않는다. 현재 노드의 설치 버전과
검증 결과는 [구현·검증 보고서](IMPLEMENTATION_AND_VALIDATION_2026-09-22.md)를 따른다.

## 빌드

저장소 루트에서 실행한다. Podman, kubectl, Helm, SSH, Python 3.12, gcc가 필요하다.
그래프 생성에만 matplotlib 3.9.4를 사용한다. 컨테이너 이미지는 노드 로컬 저장소에
배포했으며 GitHub에 바이너리 이미지를 게시하지 않았다.

```sh
mkdir -p .local/reproduce-qemu
curl --fail --location \
  https://kojihub.stream.centos.org/kojifiles/packages/qemu-kvm/10.1.0/20.el9/src/qemu-kvm-10.1.0-20.el9.src.rpm \
  --output .local/reproduce-qemu/qemu-kvm.src.rpm
podman build -f images/flyt/Qemu.Containerfile \
  -t localhost/flyt-virt-launcher:reproduce .local/reproduce-qemu
podman build -f images/flyt/Containerfile --target control-plane \
  -t localhost/flyt-control-plane:reproduce .
podman build -f images/flyt/Containerfile --target worker \
  -t localhost/flyt-worker:reproduce .
podman build -f images/flyt/Containerfile --target build \
  -t localhost/flyt-build:reproduce .
podman build -f images/flyt/Hook.Containerfile \
  --build-arg KUBEVIRT_SHIM_IMAGE=quay.io/kubevirt/sidecar-shim@sha256:8eb25c016c09366ea4b2bbd524dfbb4872184a9aea67cc34c60ff78a11cb2661 \
  -t localhost/flyt-hook:reproduce .
```

Containerfile은 source RPM의 SHA256을 검증한다. OS 패키지 저장소는 snapshot으로
고정하지 않았으므로 재빌드 image digest가 같아진다고 보장하지 않는다. 실제 실행에
사용할 이미지 digest와 게스트 `.so`·프로그램 해시를 새로 기록한다.

`build` stage의 `/opt/flyt/lib/libflyt_guest.so`를 실험 artifact 디렉터리로 복사하고,
동일 stage의 gcc와 `/usr/local/cuda/include`로 `guest_gpu_smoke.c` 및
`memory_probe.cu`를 C 프로그램으로 컴파일한다. `-lflyt_guest`에 연결하고 실행 파일과
같은 디렉터리의 `.so`를 찾도록 `-Wl,-rpath,'$ORIGIN'`을 사용한다. nvcc가 생성한
네이티브 CUDA kernel을 이 PTX smoke에 혼합하지 않는다.

```sh
mkdir -p .local/reproduce-artifacts
podman run --rm -v "$PWD:/repo:ro" -v "$PWD/.local/reproduce-artifacts:/out" \
  localhost/flyt-build:reproduce sh -ec '
    cp /opt/flyt/lib/libflyt_guest.so /out/
    gcc -O2 -I/usr/local/cuda/include /repo/experiments/evidence/guest_gpu_smoke.c \
      -L/opt/flyt/lib -Wl,-rpath,\$ORIGIN -lflyt_guest -o /out/guest-gpu-smoke
    gcc -x c -O2 -I/usr/local/cuda/include /repo/experiments/evidence/memory_probe.cu \
      -L/opt/flyt/lib -Wl,-rpath,\$ORIGIN -lflyt_guest -o /out/memory-probe-guest
  '
```

## 설치와 신규 VM

1. `flyt-evidence` namespace와 신규 실험용 TLS를 준비한다. 아래 예시는 새 설치용이며,
   기존 Secret이 있으면 재생성하지 않고 해당 CA와 Secret을 사용한다.
2. 각 이미지를 `.local/*.oci`로 `podman save --format oci-archive`하고
   `scripts/import-evidence-image.py ARCHIVE --output NEW_DIRECTORY`로 CRI-O에 import한다.
   이 도구는 gpu-4에 임시 privileged 관리 Pod를 생성해 지정 OCI만 import한 뒤 삭제한다.
   `archive-identity.json`의 manifest digest와 노드에 등록된 digest를 확인한다.
   `podman save`의 압축/manifest 변환으로 빌드 저장소의 digest와 달라질 수 있다.
3. `scripts/set-evidence-launcher.py --image localhost/flyt-virt-launcher@sha256:DIGEST --output NEW_DIRECTORY`로
   KubeVirt의 새 VMI launcher를 선택한다. 출력의 `restore-patch.json`은 변경 직전 설정의 복원 자료다.
4. GPU UUID와 기존 소유 상태를 재확인한다. 이번 검증은 GPU 1의
   `GPU-7d708c42-8d4a-16d5-0746-474567157aa3`을 사용했다. VFIO 전환은 수행하지 않았다.
5. local PV 경로 `/var/lib/flyt-evidence-20260922/a`, `/var/lib/flyt-evidence-20260922/b`는
   디렉터리 소유자 107:107, mode 0770으로 준비한다. 기존 회수 보류 PVC를 사용하지 않는다.
6. 신규 실험 SSH 키를 만든 뒤 `prepare-evidence-vm.py --help`의 image digest와 공개키를
   전달한다. 처음에는 `--storage-slot a` 또는 `b`, 이후에는 `--reuse-pvc PVC_NAME`을 쓴다.
   기존 Channel이 Released가 아니면 재사용 도구가 거절한다.

```sh
kubectl create namespace flyt-evidence
python3 scripts/create-review-tls.py --namespace flyt-evidence --release evidence \
  --output .local/reproduce-tls
kubectl create secret tls flyt-admission-tls -n flyt-evidence \
  --cert=.local/reproduce-tls/tls.crt --key=.local/reproduce-tls/tls.key
```

이미지 import를 마친 뒤 active controller를 설치한다. 아래 값은 이번 노드의 실제
실행 digest이며 새 빌드에는 새 OCI digest를 사용한다. `image.repository`도 로컬 이름과
일치시킨다. 사용자 환경의 공개 registry 이름으로 자동 변환되지 않는다.

```sh
python3 scripts/install-control-plane.py --namespace flyt-evidence --release evidence \
  --image-digest sha256:a60f76638461c0fb15c11d89779c482e44621ab52119e682b279de0dd2a4859d \
  --ca .local/reproduce-tls/ca.crt --values experiments/evidence/gpu-4-development-values.yaml
```

`set-evidence-launcher.py`는 설치된 KubeVirt의 virt-controller 인자 구성을 대상으로 한다.
다른 KubeVirt 버전에는 새 검증이 필요하다. restore patch를 여러 번 만들었다면 원하는
변경 전 상태에 대응하는 파일을 사용한다. 가장 최근 patch가 최초 상태를 뜻하지 않는다.

## 실행 및 판정

```sh
kubectl wait -n flyt-evidence flytsharedmemorychannel/NEW_VM-channel \
  --for=jsonpath='{.status.phase}'=BackingReady --timeout=60s
python3 scripts/run-evidence-smoke.py --name NEW_VM \
  --key LOCAL_PRIVATE_KEY --artifacts ARTIFACT_DIRECTORY --output NEW_RUN_DIRECTORY
```

VM 이름은 `evidence-`로 시작해야 한다. artifact 디렉터리에는 `libflyt_guest.so`,
`guest-gpu-smoke`, `memory-probe-guest`가 있어야 한다. runner는 원격 실행 결과와 Channel
Ready를 확인한 뒤 drain하고 실제 Released까지 기다린다. 정상 probe에 실패하면 FAIL로
기록하되, 회수 성공 여부를 별도 필드에 남긴다.

메모리에는 `--probe memory --scenario suite --bytes QUOTA_BYTES`를 추가한다.
`suite`는 조회값의 quota 일치, 잔여량 아래·경계·경계+1 byte, OOM 후 재할당과 계상 복구를
검사한다. `--scenario aggregate_race`는 VM 생성 시 `--sessions 2`가 필요하다.
양쪽 모두 OOM이면 개별 할당 성공 전제가 없으므로 합산 제한 입증으로 세지 않는다.

`repeat-evidence-smoke.py`는 최대 20개 신규 VM/allocation을 두 독립 PVC에서 반복한다.
`--images` JSON의 `control`, `worker`, `hook` 값에는 실제 사용한 digest-pinned image를
넣는다. `--artifacts` 디렉터리의 키 파일명은 `guest-key`, `guest-key.pub`이다.

`fault-evidence-smoke.py`는 두 VM의 실제 GPU 연산을 장벽으로 시작하고 Guest process,
Worker process/Pod, VMI 또는 실험용 controller에만 지정 장애를 주입한다. 종료 대상
일반 smoke의 FAIL과 장애 시나리오의 PASS는 별도 판정이다. VM B의 연산 성공 및 두
Channel 회수가 필요하다. 이 도구의 PTX 결과를 학습 정확성 또는 노드 단절 증거로 쓰지 않는다.

## 검증과 공개 자료

```sh
python3 -m unittest discover -s tests/control -v
python3 -m unittest discover -s experiments/evidence/tests -v
python3 experiments/evidence/export_development.py --source PRIVATE_RUN_ROOT --output NEW_PUBLIC_DIRECTORY
python3 experiments/evidence/plot_development.py --lifecycle LIFECYCLE_RUN_DIRECTORY --output PLOT_DIRECTORY
```

tensor 비교 검사를 포함하려면 CPU PyTorch가 있는 Python 환경에서 단위 검사를 실행한다.
CUDA를 직접 쓰지 않는 `tests/integration/allocation_oom_recovery.c`는
`experiments/cuda-dispatch/src/exec.c`와 연결해 실행한다.
`memory_info_dispatch.c`는 CUDA 개발 이미지에서 `runtime/shm/src/dispatch.c`와 연결하며
실제 CUDA 조회 대신 오류·경계 동작을 제어하는 mock을 사용한다.

공개 exporter는 metrics/timeline/probe 출력 및 제한된 binding·image 식별자만 복사한다.
원시 `.local` 디렉터리에는 SSH/TLS 키가 있으므로 전체 압축·업로드하지 않는다.
