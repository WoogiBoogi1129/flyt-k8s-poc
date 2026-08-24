# Baseline 대비 변경 목록

| Patch | 분류 | 변경 목적 | 대표 검증 |
|---|---|---|---|
| `flyt-k8s-config-paths` | Kubernetes | manager 설정을 `/etc/flyt` 절대 경로에서 읽음 | Pod/VM cwd와 무관한 시작 |
| `flyt-ipc-ftok` | 안정성 | IPC `ftok` 입력의 NUL 종료 보장 | client manager 연결 |
| `flyt-cudnn9` | 빌드 호환 | cuDNN 9에서 제거된 legacy API 조건부 처리 | CUDA 12.8 이미지 빌드 |
| `flyt-pytorch-driver-entry` | Runtime API | 최신 PyTorch driver entry/event/launch 경로 보완 | import, 기본 tensor/kernel |
| `flyt-source-eof` | 재현성 | 소스 EOF 정규화 | patch byte 재현 |
| `flyt-pytorch-host-memory*` | Runtime API | TCP fallback pinned host memory 추적과 복사 | pinned memory round trip |
| `flyt-pytorch-event-map` | Runtime API | event lookup의 성공/실패 판정 수정 | autograd/cuBLAS/AMP |
| `flyt-mig-discovery` | MIG/K8s | NVML 물리 열거 대신 Pod에 노출된 CUDA 장치 탐색 | MIG 1개, 기대 SM 확인 |
| `flyt-mig-cuda-accounting` | MIG | CUDA property 기반 SM·메모리 등록 | Cluster Manager 등록값 |
| `flyt-core-api-set-device` | CUDA semantics | 논리 device 0만 허용 | invalid device 오류 코드 |
| `flyt-core-api-stream-create` | CUDA semantics | stream 생성 시 불필요한 module 재적재 제거 | stream/event 생성 |
| `flyt-mig-memory-quota` | 자원 통제 | 서버 로컬 GPU allocation accounting | 8 GiB quota OOM 경계 |
| `flyt-runtime-function-map` | Runtime API | client 함수 토큰을 서버 `CUfunction`으로 변환 | SDPA 관련 attribute 경로 |
| `flyt-pytorch-build-profile` | 빌드 | PyTorch 2.11, CUDA 12.8, shared cudart, sm_120 | custom wheel 생성 |

패치의 원문과 적용 순서는 각각 `patches/*.patch`, `patches/series`가 유일한
기준이다. 배포 YAML에 patch 내용을 복제하지 않고 ConfigMap을 실행 시 생성한다.
