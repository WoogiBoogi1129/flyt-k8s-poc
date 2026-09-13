# Stage 1 — standalone HAMi quota PoC

**구현 상태: implemented-unvalidated. 빌드·렌더링·정적 검사·CI·GPU 시험 모두 NOT_RUN.**

이 디렉토리는 FLYT를 변경하기 전에 HAMi-Core만으로 정적 메모리/연산 quota를
측정하기 위한 독립 실험이다. 소스 및 실행 도구를 작성한 단계이며, 설치 성공이나
quota 동작을 입증한 결과는 아직 없다. 아래 명령은 향후 실행 절차이지 실행 기록이 아니다.

```text
CUDA probe Pod → HAMi-Core (libvgpu.so) → NVIDIA Driver → GPU
```

KubeVirt, FLYT client/server, Cricket RPC, CUDA MPS는 이 실험에 포함하지 않는다.
기존 `patches/`, `deploy/`, `images/flyt/`, `probes/`의 실행 경로는 유지한다.
단계 관리 원칙은 [DEVELOPMENT_STAGES.md](../../docs/DEVELOPMENT_STAGES.md)를 참고한다.

## 환경과 GPU 소유권

- 대상은 MIG를 사용하지 않는 physical GPU 한 개다. index 대신 UUID로 지정한다.
- CUDA 12.8.1, 기본 빌드 아키텍처 `sm_120`, HAMi chart 2.8.0 / image v2.8.0을
  개발 기준으로 고정했다. Blackwell/driver 조합에서 이 버전의 HAMi가 정상 동작하는지는 미검증이다.
- 기본 예제 kube-scheduler는 v1.29.12이며 실제 서버 버전과 일치해야 한다.
  DRA를 사용하지 않으므로 이 실험을 위해 DRA 또는 KubeVirt를 설치할 필요는 없다.
- NVIDIA container runtime이 이미 구성되어 있어야 한다. 기존 RuntimeClass를
  사용하거나 NVIDIA runtime이 기본인 환경에서만 빈 `runtime_class`를 사용한다.
  이 도구는 runtime, driver, MIG mode, 노드 라벨을 변경하지 않는다.
- 외부 ML Platform이 GPU를 등록/예약하고 있으면 idle 상태라도 사용 승인이 아니다.
  `platform_release_confirmed`는 관리자가 실제로 대상 GPU를 해제/예약한 뒤에만 true로 설정한다.
- 현재 확인된 외부 `ixgpu` 공유 노드에는 이 installer를 실행할 수 없다.
  같은 노드에서 다른 device plugin이 kubelet socket을 사용하면 UUID를 나눠도
  충돌할 수 있다. installer는 다른 plugin과 `gpu-share=true`를 발견하면 중단한다.
  **기존 GPU 0/1을 관리하는 plugin을 중단해서 이 검사를 통과시키지 않는다.**
  현 장비에서 다른 GPU 구성을 유지하며 공존시키는 작업은 별도 플랫폼 통합 과제다.
  가장 단순한 검증 환경은 다른 GPU plugin이 없는 별도 노드/클러스터다.

`node_gpu_uuids`에는 대상 노드의 전체 GPU UUID를 기록한다. 렌더러는 대상 이외
UUID를 HAMi의 `filterdevices.uuid` **제외 목록**에 넣고, 시험 Pod에는
`nvidia.com/use-gpuuuid`를 추가한다. 실행 직전 실제 inventory와 설정이 다르면 중단한다.
장치 추가/교체 후에는 설정을 다시 작성해야 하며, 실행 중 hot-plug는 지원하지 않는다.
HAMi 관리 컴포넌트가 NVML로 다른 GPU를 조회할 수는 있지만, 다른 GPU를 등록하거나
해당 GPU의 MIG/전력/compute mode를 변경하는 명령은 제공하지 않는다.

## 파일

| 파일 | 역할 |
|---|---|
| `versions.json` | 기준 소스 커밋, HAMi/CUDA 버전, 미검증 상태 |
| `config.example.json` | 대상 context/node/UUID, image digest, 시험 조건 |
| `Containerfile` | shared cudart를 사용하는 독립 CUDA probe 이미지 |
| `probe.cu` | 환경 확인, 메모리 경계/반환, 고정 workload 연산 측정 |
| `stage1.py` | render / preflight / install / run / cleanup |
| `helm-post-render.py` | HAMi webhook을 실험 namespace와 라벨로 제한 |

## 향후 준비 절차

저장소 루트에서 실행한다. 아래 명령은 현재 개발 작업에서는 실행하지 않았다.
Python 3.10+, Helm 3, 대상 서버와 호환되는 kubectl, nvidia-smi가 필요하다.
이미지 빌드·push에는 Docker와 레지스트리 접근 권한이 별도로 필요하다.

```bash
python3 -m venv .local/hami-stage1-venv
.local/hami-stage1-venv/bin/pip install -r experiments/hami-standalone/requirements.txt
mkdir -p .local/hami-stage1
cp experiments/hami-standalone/config.example.json .local/hami-stage1/config.json
```

설정의 모든 `REPLACE_*` 값을 채운다. 개인 context와 UUID는 `.local/` 안에만 저장한다.
`probe_image`는 아래 빌드·배포 후 확인한 실제 digest로 채운다. 레지스트리 push는
해당 레지스트리에 대한 권한이 있는 환경에서 수행한다.

```bash
docker build -f experiments/hami-standalone/Containerfile \
  --build-arg CUDA_ARCH=sm_120 \
  -t YOUR_REGISTRY/flyt/hami-stage1:stage-01 experiments/hami-standalone
docker push YOUR_REGISTRY/flyt/hami-stage1:stage-01
```

다른 GPU architecture에서는 arch와 시험 환경을 함께 수정하고 별도로 검증한다.
CUDA base image는 digest로 고정되어 있다. HAMi/chart는 버전 고정이며 바이트 단위의
불변성을 보장하는 digest/archive 잠금은 아직 제공하지 않는다. 설치 로그와 실행 시
관리 컴포넌트/시험 Pod의 imageID를 보존하여 실제 사용 버전을 확인한다.

## 렌더링과 설치

```bash
export PATH="$PWD/.local/hami-stage1-venv/bin:$PATH"
python experiments/hami-standalone/stage1.py render \
  --config .local/hami-stage1/config.json
python experiments/hami-standalone/stage1.py preflight \
  --config .local/hami-stage1/config.json
python experiments/hami-standalone/stage1.py install \
  --config .local/hami-stage1/config.json
```

- `render`는 로컬 파일만 생성한다. GPU/클러스터에 접근하지 않는다.
- `preflight`는 설정한 context와 로컬 GPU inventory를 읽는다. 대상 GPU 호스트에서 실행해야 한다.
  기존 GPU 예약/프로세스/MPS, 다른 device plugin, inventory 및 서버 버전 불일치를 거부한다.
  이 검사는 실행 직전의 관측이며 플랫폼 소유권 예약 자체를 대신하지 않는다.
- `install`은 preflight 후 **별도 namespace와 Helm release를 생성/업데이트**한다.
  HAMi 설치에는 ClusterRole/Binding과 webhook 등 클러스터 범위 객체도 포함된다.
  기존 FLYT namespace나 기존 GPU plugin을 수정하지 않는다.
- Helm post-renderer는 webhook 대상을 이 namespace의 `flyt.dev/experiment=hami-stage1`
  Pod로 한정한다. `hami-values.yaml`만 가지고 post-renderer 없이 수동 설치하면 안 된다.
- device plugin은 target hostname만 선택한다. 다른 벤더 device plugin 및 DRA는 비활성화한다.
- 설치/업데이트 중 기존 GPU workload가 있으면 중단한다. 설치 명령의 자동 rollback은
  수행하지 않으므로 실패 시 해당 release의 상태를 조사한 뒤 처리한다.
- installer는 namespace를 자동 삭제하지 않으며 runtime/driver 재시작도 수행하지 않는다.

## 시험과 판정

```bash
python experiments/hami-standalone/stage1.py run \
  --config .local/hami-stage1/config.json
```

시험은 Job을 **순차적으로** 생성한다. 단일 GPU를 사용하는 동시 workload를 두지 않는다.
메모리 시험 1회 다음에 100%/30% 비교를 기본 3쌍 수행하며, 쌍마다 순서를 번갈아 사용한다.
한 Job이 끝나면 결과를 수집한 뒤 해당 Job과 Pod를 제거한다. 다음 trial 전에 다시
프로세스와 예약을 확인하며 잔여 상태가 있으면 실패로 남긴다.

각 probe는 다음 조건을 확인한 후에 측정한다.

- CUDA-visible device가 정확히 하나이며 실제 UUID가 지정한 대상과 일치한다.
- `/proc/self/maps`에 HAMi의 `libvgpu.so`가 로드되어 있다.
- Flyt/Cricket 라이브러리가 매핑되지 않았고 MPS 설정 환경변수가 없다.
- 호스트 preflight에서 대상 GPU에 기존 MPS/compute 프로세스가 없다.

이는 interception 환경의 필요조건이지 모든 CUDA API가 quota 제어를 통과한다는
증명은 아니다. 실제 allocation/compute 결과와 함께 판정한다.

### 메모리

기본값: quota=8192 MiB, chunk=64 MiB, headroom=1024 MiB.

1. 64 MiB씩 누적 할당하고 실제 메모리를 touch/synchronize한다.
2. OOM 시 누적 성공량이 `[7168, 8192] MiB` 안에 있어야 한다.
3. quota보다 큰 양을 할당할 수 있거나 너무 이른 OOM, 다른 CUDA 오류이면 실패한다.
4. 메모리를 전부 반환한 후 새 chunk를 할당할 수 있어야 한다.

CUDA context 등의 오버헤드 때문에 `cudaMalloc(8 GiB)` 성공을 요구하지 않는다.
headroom은 실험 전에 고정하며, 실패 결과를 본 뒤 근거 없이 늘려 PASS로 바꾸지 않는다.
프로세스 종료/다른 Pod 사이의 quota 회수와 사용자 간 격리는 후속 단계에서 검증한다.

### 연산

기본값: warm-up 5초, 측정 30초, 동일한 1024 blocks × 256 threads × 8192 FMA 반복.
HAMi 제한을 포함하는 **host wall time**으로 launches/second를 계산하고 각 quota의
중앙값을 비교한다. GPU event 시간만 사용하면 host-side throttling을 놓칠 수 있다.

`GPU_CORE_UTILIZATION_POLICY=force`와 `nvidia.com/gpucores`를 함께 지정한다.
`gpucores=30`은 30% 물리 SM partition 또는 정확히 0.30배 throughput을 의미하지 않는다.
기본 acceptance interval은 30%/100% 중앙값 비율 `[0.10, 0.65]`다. 이는 초기 실험용
판정 범위이며 HAMi의 성능 보증이 아니다. 시험 전에 합의한 범위를 config에 기록한다.
비율 이외에도 각 trial 시간·launch 수·GPU clock/공유 부하 영향을 함께 검토해야 한다.

## 결과와 정리

결과는 기본 `.local/hami-stage1/<run-id>/`에 기록된다.

- `config.json`, `versions.json`, `preflight.json`: 요청한 조건과 실행 전 환경
- `runtime-pods.json`: HAMi 관리 컴포넌트의 실행 이미지 및 상태
- trial별 `requested.json`, `job.json`, `pods.json`, `events.txt`, `probe.log`, `result.json`
- trial별 `gpu-before.txt`, `gpu-after.txt`: 대상 GPU의 driver/clock/power/memory 등 관측값
- `summary.json`: RUNNING/PASS/FAIL/ERROR, 모든 측정값, 중앙값 비율, 판정 범위

실행하지 않은 결과를 생성하지 않는다. `probe` 단독 PASS는 전체 1단계 PASS가 아니다.
환경/스케줄링/로그 오류는 `ERROR`, quota 비율 미달은 `FAIL`로 남긴다.
중간 오류에서도 이미 수집된 결과와 summary를 보존한다.

중단 때문에 남은 Job은 정확한 run ID로만 삭제한다.

```bash
python experiments/hami-standalone/stage1.py cleanup \
  --config .local/hami-stage1/config.json --run-id RUN_ID_FROM_SUMMARY
```

cleanup은 해당 namespace에서 owner/run 라벨이 모두 일치하는 Job만 지운다.
HAMi 자체를 제거하려면 먼저 모든 시험 Pod 종료를 확인하고, 설치에 사용한 context에서
`helm uninstall flyt-hami-stage1 --namespace <experiment-namespace> --kube-context <context>`를
별도로 실행한다. namespace/host hook 파일은 자동 삭제하지 않는다. 다른 release를
uninstall하거나 공용 device plugin 디렉토리를 지우는 복구 절차는 사용하지 않는다.

## 남은 검증

- Python 구문/정적 검사, Helm 렌더링 및 webhook 제한 확인
- CUDA 컴파일, shared cudart 및 HAMi preload 호환성
- HAMi 2.8.0 + CUDA 12.8 + Blackwell + 대상 드라이버 조합
- device exclusion이 GPU 등록·할당에 반영되고 다른 GPU의 설정이 보존되는지 확인
- 실제 runtime에서 UUID/property 조회가 대상 GPU를 올바르게 반환하는지 확인
- 메모리 quota 경계, 30%/100% 비교, 실패 시 로그와 정리 동작
- 설치 재실행, 이미지 변경, 프로세스 중단 및 quota 반환

현재는 위 항목을 하나도 실행하지 않았다. 1단계가 미검증인 동안 후속 브랜치에서
작업할 수는 있지만, FLYT의 MPS 제거 또는 HAMi 검증 완료를 주장할 수는 없다.

## 근거

- [HAMi 2.8.0 chart values](https://github.com/Project-HAMi/HAMi/blob/v2.8.0/charts/hami/values.yaml)
- [HAMi 2.8.0 NVIDIA device filtering/UUID selection](https://github.com/Project-HAMi/HAMi/blob/v2.8.0/pkg/device/nvidia/device.go)
- [HAMi 2.8.0 webhook template](https://github.com/Project-HAMi/HAMi/blob/v2.8.0/charts/hami/templates/scheduler/webhook.yaml)
- [HAMi quota 실험 안내](https://project-hami.io/tutorials/labs/gpu-partitioning)
