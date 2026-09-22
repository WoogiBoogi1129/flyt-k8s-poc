# SHM/HAMi 실증 착수 결과 — 2026-09-22 KST

> 이 문서는 최초 착수 시점의 기록이다. 이후 대표 PyTorch SHM 학습과 실제
> passthrough 기준 검증을 수행했다. 최신 상태와 남은 항목은
> [PyTorch 구현·검증 보고서](PYTORCH_IMPLEMENTATION_2026-09-22.md)를 따른다.

**실증 도구 구현과 독립적인 HAMi 예비 검증은 수행했다. VM 기반 본 실험은
선행 개발 미완료로 차단됐으며, 전체 실증 완료를 주장하지 않는다.**

## 이번 변경

- 고정 설정, 294개 사례의 행렬, 소스/설정 해시, 실노드 inventory와 차단 보고서 구현.
- FP32 eager MLP·SGD fixture와 학습 실행, 1/100 step loss·gradient·parameter 비교 구현.
- 고정 작업량·전송 포함·별도 latency·완료 chunk window, 두 guest 장벽 및 timeout 취소 구현.
- 성능비·VM별 slowdown·clock 불확실성을 반영한 공통 구간 분석 구현.
- cgroup CPU 계측, 직접 CUDA 메모리·다중 프로세스 경쟁 probe, HAMi smoke 실행기 구현.
- 남은 개발/배포/명세 작업을 `DEVELOPMENT_GATES.md`에 분리.

기존 `runtime/shm/src/mapping.c` 수정과 설치 기록은 보존했다. `.gitignore`의
`evidence/`를 `/evidence/`로 한정해 신규 `experiments/evidence/` 소스가 버전 관리에
포함되도록 했다. 원시 결과는 계속 `.local`에 보관한다.

## 실제 관측과 수행 결과

| 확인 항목 | 결과 | 해석 |
|---|---|---|
| 노드 | gpu-4 Ready, Kubernetes 1.37.0 / CRI-O 1.37.0 | 실노드 API 조회 |
| GPU | RTX PRO 6000 Blackwell Server Edition 4장, driver 580.173.02 | nvidia-smi 조회 |
| 실험 후보 GPU | GPU 1, UUID 끝 `474567157aa3`, NUMA 0, BDF 0000:41:00.0, IOMMU 그룹 26 단독 | VFIO 전환은 미수행 |
| QEMU | 실행 중인 basic-vm launcher의 `-device help`에 ivshmem-plain 없음 | VM SHM 실험 진입 BLOCKED |
| PyTorch SHM 호환성 | source에서 Runtime launch 거절, fullPyTorch NOT_IMPLEMENTED | 실제 MLP GPU 실행은 미수행 |
| 새 HAMi 예비 검증 | PASS | 아래 독립 실행의 한정된 범위 |
| 신규 도구 검사 | 15개 PASS, skip 없음 | tensor 오류 검출·직렬화·측정 모드·장벽/취소 포함 |
| 기존 control-plane 검사 | 10개 PASS | CPU 단위 검사이며 GPU 기능 검증은 아님 |
| CPU MLP 실물 checkpoint 비교 | 1/100 step의 loss·gradient·parameter 18개 비교 PASS, 최대 절대오차 0 | CPU harness 검증 전용 |
| memory_probe | CUDA 12.8 빌드 성공 | 실제 VM 메모리/경쟁 시험은 미수행 |

HAMi 예비 검증은 `flyt-evidence` 신규 namespace에서 GPU 1만 요청해 수행했다.
사용 image는 `nvidia/cuda:12.8.1-base-ubuntu22.04`의 고정 digest이며 정확한 값은
run manifest에 저장했다. 기존 소스 `hami-smoke.cu`를 로컬 CUDA 빌드 이미지에서
다시 빌드한 바이너리를 사용했다.

실제 출력:

```text
GPU-7d708c42-8d4a-16d5-0746-474567157aa3
GPU_COUNT=1 FREE_BYTES=497025024 TOTAL_BYTES=1073741824
CUDA_RESULT=42
OVER_QUOTA_ALLOCATION=out of memory
PASS: CUDA computation and memory quota enforcement
```

1024 MiB 요청에서 1536 MiB 할당 거절을 확인했다. 연산 25 설정의 실제 제한 비율,
VM 합산 quota, SHM 학습 정확성을 검증한 결과는 아니다. 보고된 여유 메모리는 약
474 MiB였으며, quota 전부를 사용자 할당 가능량으로 가정하지 않아야 한다.
예약량의 원인은 이번 smoke만으로 확정하지 않았다.

테스트 Pod·ConfigMap은 생성 UID를 확인한 뒤 삭제했다. 종료 후 GPU 사용 메모리는
4장 모두 0 MiB였다. `flyt-evidence` namespace와 Kubernetes 기본 CA ConfigMap만
남겼다. 기존 `shm-channel-a`는 Draining, `shm-channel-b`는 Released 상태를 유지한다.
기존 VM, GPU 모드, VFIO binding, finalizer를 변경하지 않았다.

## 검사 중 발견·수정한 문제

첫 CPU checkpoint 비교는 `torch.__version__`의 TorchVersion 객체가
`weights_only=True` 로딩에서 거절돼 실패했다. 버전 필드를 일반 문자열로 저장하도록
고쳤다. 이후 새 출력 디렉터리에서 재실행했고 비교가 통과했다. 이전 실패 결과도
개발 증거로 보존했다. 임의 pickle 로딩 허용으로 우회하지 않았다.

NaN/Inf·누락 gradient·잘못된 fixture·누락 checkpoint·무한대 tolerance를 거절하는
검사를 추가했다. 예비 시간 누락/중복, 다른 GPU/소스 버전 혼합, CPU 결과의 GPU
성능 편입, 잘못된 반복 블록도 검사한다. CPU PyTorch 2.6.0+cpu는 별도
`.local/evidence-venv`에 설치했고 GPU용 PyTorch 버전 결정과 분리했다.

## 본 실험 장부

| 실험 | PASS | FAIL | BLOCKED | NOT_RUN |
|---|---:|---:|---:|---:|
| E1 학습 정확성 | 0 | 0 | 24 | 0 |
| E2 자원 제한·정상 생명주기 | 0 | 0 | 43 | 0 |
| E3 단일 VM 성능 | 0 | 0 | 60 | 0 |
| E4 2개 VM | 0 | 0 | 120 | 0 |
| E5 격리 장애 및 조건부 노드 단절 | 0 | 0 | 45 | 1 |
| E6 조건부 다중 GPU | 0 | 0 | 0 | 1 |

필수 292개는 선행 조건이 충족되지 않아 BLOCKED다. 노드 단절과 다중 GPU는
조건부 2개로 NOT_RUN이다. BLOCKED는 실행 실패를 뜻하지 않으며, 성능 수치는 없다.

QEMU ivshmem, 대표 PyTorch 호환성, 일반 종료 증거 보존, passthrough/RPC·MPS
baseline, VM·Worker CPU 배치, quota/compute 판정 계약, 격리 장애 fixture가 남았다.
이 때문에 현재 결과를 GPU 공유 시스템의 정확성·성능 검증 완료로 사용할 수 없다.

## 증거 위치와 재개 방법

원시 결과 디렉터리: `.local/evidence-20260922/`

- `manifest.json`, `config.json`, `source.json`, `source-final.json`: 단계·설정·소스 기록.
- `preflight/`: GPU·NUMA·IOMMU·QEMU·VM/Pod·Channel 관측.
- `matrix.json`, `cases/*/{manifest,metrics}.json`, `summary.json`, `REPORT.md`: 전체 실험 장부.
- `development/hami-smoke/`: 실제 GPU smoke manifest·로그·자원 정리 기록.
- `development/cpu-reference-v2/`, `cpu-candidate-v2/`, `cpu-comparison-v2.json`: 수정 후 CPU 검증.
- `harness-tests-final.txt`: 신규 15개 검사 결과.

이 결과 디렉터리는 접근 자격증명을 포함하는 이전 installation `.local` 디렉터리와
분리돼 있다. 공유 시에는 위 증거 파일만 명시적으로 선택한다.

선행 개발 종료 후 **새 실험 디렉터리**에서 preflight를 수행하고 이미지·소스·설정을
동결한다. E1 결과와 전체 비교군의 예비 시간을 확보한 뒤 E3/E4를 실행한다. 현재
장부의 BLOCKED를 성공으로 바꾸거나 CPU/개발 수치를 본 실험으로 재분류하지 않는다.
