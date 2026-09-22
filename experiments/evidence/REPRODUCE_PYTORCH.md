# PyTorch SHM 경로 개발 검증 재현

이 절차는 고정 FP32 eager MLP·SGD의 개발 검증이다. 본 실험의 5회 독립 반복,
CPU 배치 통제, RPC·MPS 비교를 대신하지 않는다. 실제 실행 결과와 실패 이력은
[구현 보고서](PYTORCH_IMPLEMENTATION_2026-09-22.md)를 참조한다.

## 빌드와 입력

[기존 VM 준비 절차](REPRODUCE_VM_DEVELOPMENT.md)의 controller·hook·ivshmem
launcher 설치와 신규 PVC 준비를 먼저 완료한다. 이번 Worker는 수정된
`images/flyt/Containerfile`의 `worker` target으로 다시 빌드한다. Guest shim도 같은
소스에서 빌드하고 실제 사용한 이미지 digest와 `.so` 해시를 각각 기록한다.

필요 환경은 CUDA 12.8.1/cuDNN 개발 이미지, Python 3.10, 저장소의
`artifacts/torch-2.11.0+flyt.cu128-cp310-cp310-linux_x86_64.whl`이다.
해당 wheel은 별도 배포 artifact이며 Git 소스만으로 자동 재생성되지 않는다.
임의의 upstream PyTorch wheel로 바꾼 실행은 같은 검증 버전으로 취급하지 않는다.

```sh
cmake -S runtime/shm -B /work/shm-build -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build /work/shm-build --parallel 8
bash scripts/test-shm-training.sh /work/shm-build
python3 -m unittest discover -s tests/control -q
python3 -m unittest discover -s experiments/evidence/tests -q
```

위 C 검사는 CUDA 헤더와 native Driver 라이브러리가 필요하지만 GPU를 실행하지 않는다.
마지막 Python 검사는 CPU PyTorch가 설치돼 있어야 tensor 검사까지 수행한다.

Guest 배포용 tar.gz는 Ubuntu 22.04/Python 3.10 환경에서 준비한다.
`/opt/torch-venv`에 고정 wheel과 의존성을 설치하고, `/opt/flyt-cuda-libs`에 같은
CUDA 12.8 사용자 라이브러리 및 cuDNN을 둔다. venv의 Python은 guest의 Python 3.10에
연결될 수 있어야 한다. tar 내부 경로는 `opt/torch-venv`, `opt/flyt-cuda-libs`여야 한다.
shim artifact 디렉터리에는 `libflyt_guest.so`, `libnuma.so.1`, `libgomp.so.1`을 둔다.

```sh
python3 experiments/evidence/train.py fixture --seed 2026 --out fixture-2026.pt
python3 experiments/evidence/train.py fixture --seed 2027 --out fixture-2027.pt
```

fixture는 고정 wheel 환경에서 한 번 생성한 뒤 모든 VM에 동일 파일을 복사한다.
`config.json`과 `train.py`도 동일해야 한다. 공통 프로그램이 모든 방식에
`DISABLE_ADDMM_CUDA_LT=1`, SGD `foreach=False, fused=False`를 적용한다.

기존 2 GiB containerDisk는 배포 파일을 담기 부족하다. 기존 digest 고정 Ubuntu
containerDisk에서 `disk.qcow2`를 추출해 **실행하지 않은 복사본**을
`qemu-img resize disk.qcow2 16G`로 늘린 뒤 아래 이미지를 만든다.
cloud-init의 growpart/resizefs 완료와 root 가용 공간 5 GiB 이상을 확인한다.

```sh
podman build -f images/flyt/EvidenceGuest.Containerfile \
  -t localhost/flyt-evidence-guest:reproduce DIRECTORY_CONTAINING_DISK
```

이미지는 기존 `import-evidence-image.py`로 노드에 import하고, 반환된 digest를 사용한다.
노드 image GC가 로컬 이미지를 제거할 수 있으므로 재실행 직전에 launcher·hook·Worker·guest
모든 digest의 존재를 확인한다. 장기 운영에는 동일 digest를 다시 pull할 수 있는 registry를 준비한다.

## SHM 실행

```sh
python3 scripts/prepare-evidence-vm.py --name evidence-training-new \
  --reuse-pvc RELEASED_BACKING_PVC --public-key LOCAL_PUBLIC_KEY \
  --control-image CONTROL_IMAGE_AT_DIGEST --worker-image WORKER_IMAGE_AT_DIGEST \
  --hook-image HOOK_IMAGE_AT_DIGEST --guest-image GUEST_IMAGE_AT_DIGEST \
  --memory-mib 4096 --compute 100 --sessions 1 --output NEW_PREPARE_DIRECTORY
kubectl wait -n flyt-evidence flytsharedmemorychannel/evidence-training-new-channel \
  --for=jsonpath='{.status.phase}'=BackingReady --timeout=60s
python3 scripts/run-evidence-training.py --name evidence-training-new \
  --key LOCAL_PRIVATE_KEY --bundle PYTHON_CUDA_TAR_GZ --artifacts ARTIFACT_DIRECTORY \
  --fixture fixture-2026.pt --output NEW_RUN_DIRECTORY
```

runner는 새 VM을 부팅하고 파일을 배포한다. 실제 ivshmem BDF·layout·slot을 연결하고,
`FLYT_MIRROR_DEVICE_VA=1`, `LD_PRELOAD=libflyt_guest.so` 및 `libcuda.so.1` alias를 설정한다.
한 프로세스/세션에서 네 조건을 실행하며 각 조건마다 fixture·model·optimizer를 초기화한다.
Channel Ready, 실행 중 Worker와 VMI, 결과 tensor를 수집한 뒤 drain·Released·Worker 부재를
확인한다. 실패한 경우에도 자신의 allocation을 drain하고 실패 상태를 남긴다.
원시 출력에는 cloud-init 공개키와 배포 정보가 있을 수 있으므로 디렉터리 전체를 게시하지 않는다.

두 VM에는 서로 다른 PVC/allocation과 seed 2026/2027을 사용한다. 각각 compute 50,
memory 4096 MiB, sessions 1로 준비하고 두 runner에 같은 **신규** `--barrier DIRECTORY`를
준다. 두 `*.ready` 파일을 확인한 뒤 `DIRECTORY/start`를 생성한다. 이전 장벽 파일을
재사용하지 않는다. GPU 프로세스 관측을 별도로 기록해 실제 실행 중첩과 같은 UUID 사용을
확인한다. 이 장벽은 기능 검증용이며 E4의 중앙 60초 처리량 구간을 제공하지 않는다.

## Passthrough 기준

SHM allocation과 Worker를 모두 회수한 후 같은 물리 GPU를 시간적으로 전환한다.
별도 `flyt-evidence-baseline` namespace를 사용한다. `flyt-evidence`에는 SHM 요청을 요구하는
admission 정책이 있으므로 native VM을 그대로 넣으면 거절된다.

1. UUID/BDF/IOMMU 그룹, compute 프로세스, HAMi 소유 상태를 기록한다. 이 노드에서 단순
   `hami.io/device-cordon` annotation은 제외를 보장하지 못했다. 설치 device-plugin의
   nodeconfig `filterdevices.uuid`로 제외한 뒤 실제 할당 probe가 Pending인지 검증한다.
2. 대상 GPU의 NVML FD 보유자를 확인한다. device-plugin/vGPUmonitor/DCGM이 장치를 열고 있으면
   compute 작업이 없어도 NVIDIA unbind가 지연될 수 있다. 해당 노드의 모니터링 Pod를
   필요한 범위에서 재시작하며, 다른 GPU workload가 없는 실험 구간에서 전환한다.
3. IOMMU 그룹에 대상 장치만 있는지 확인하고 `vfio-pci`에 bind한다. KubeVirt
   `permittedHostDevices.pciHostDevices`에 해당 PCI vendor/device와 전용 resource를 등록한다.
   VM에는 그 resource를 요청하고 실제 launcher의 host BDF 및 guest UUID를 다시 확인한다.
4. VM은 8 vCPU/16 GiB, 같은 Ubuntu root image·Python·CUDA·wheel·fixture를 사용한다.
   guest 커널에 맞는 NVIDIA 580.173.02 open module과 GSP firmware를 설치한다.
   SHM shim/preload는 설치하지 않고 native `libcuda.so.1`을 사용한다.
5. 영구 root disk를 재사용할 때 MAC 주소를 고정한다. cloud-init networkData 변경이 필요하면
   `instance-id`로 사용되는 firmware UUID와 기존 cloud-init cache의 관계를 확인한다.
   이번에는 새 UUID와 고정 MAC으로 stale network 설정을 해소했다.
6. 각 seed·batch·입력 모드에 `train.py run --backend passthrough --mode correctness`를 실행한다.
   seed 2026의 네 조건은 프로세스를 새로 시작해 한 번 더 실행하여 기준의 반복 일치를 확인한다.
7. 결과를 회수한 후 VM을 Halted로 바꾸고 VMI/launcher가 사라진 것을 확인한다. GPU를 NVIDIA로
   rebind하고 driver_override, HAMi filter/cordon, KubeVirt의 임시 permitted device 설정을
   원래 상태로 되돌린다. 모든 GPU 등록과 device-plugin/DCGM Ready를 검증한다.

`--backend` 문자열만으로 passthrough가 입증되지 않는다. guest native driver·물리 UUID와
host VFIO/launcher BDF 증거를 결과와 연결한다. 위 노드 전환은 범용 자동화 도구로 제공하지 않는다.

## 비교·보관

```sh
python3 experiments/evidence/compare.py REFERENCE/tensors.pt CANDIDATE/tensors.pt \
  --atol 1e-6 --rtol 1e-4 --out comparison.json
```

각 조건의 1/100 step에 loss 하나, gradient 네 개, parameter 네 개를 비교한다.
비교기는 fixture·모델·정밀도·입력 조건뿐 아니라 PyTorch/CUDA 버전, 프로그램 해시,
optimizer 실행 옵션과 BLAS 경로 설정도 확인한다. tensor 원본은 로컬에 보존하고
공개 결과에는 해시·비교 요약·정제한 경로 증거를 넣는다.

CUPTI 추적은 native 진단 전용이다. `cuda_trace.c`는 `-shared -fPIC -lcupti -lcudart -ldl
-lpthread`와 CUDA/CUPTI include·library 경로로 빌드한다. `FLYT_CUDA_TRACE=NEW_FILE`과
LD_PRELOAD로 활성화하고 `summarize_cuda_trace.py`로 요약한다. 계측기가 호출한
parameter metadata API가 trace에 추가되므로 횟수를 workload의 필수 API 횟수로 읽지 않는다.
측정용 성능 실행에는 이 계측기를 preload하지 않는다.
