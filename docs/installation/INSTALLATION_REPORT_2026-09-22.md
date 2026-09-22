# Kubernetes 부가 구성 요소 설치 및 FLYT 실증 보고서

실행 기간: 2026-09-21~22 KST. 대상 노드: `gpu-4`.
브랜치: `feat/k8s-native-control-plane`, 기준 커밋: `c4b67fc`.
사용자가 완료한 2단계의 taint 제거는 다시 실행하지 않았다.

## 결과

**GPU Operator, HAMi, KubeVirt 설치와 기본 GPU·VM 동작 검증은 완료했다.
FLYT의 Guest → SHM → GPU Worker 전체 경로 검증은 완료하지 못했다.**
공식 KubeVirt launcher에 `ivshmem-plain` 장치가 없는 것이 현재 차단 원인이다.

| 항목 | 확인 결과 |
| --- | --- |
| Kubernetes / CRI-O | `v1.37.0` / `1.37.0`, 노드 Ready |
| GPU Operator 26.7.0 | ClusterPolicy `ready`, 관리 Pod 정상 |
| 기존 NVIDIA 드라이버 | `580.173.02` 유지, 물리 GPU 4개 |
| NVIDIA CUDA validator | 실제 벡터 연산 통과 |
| HAMi 2.10.0 | scheduler와 Device Plugin 모두 2/2 Ready |
| HAMi CUDA 테스트 | GPU 1개, 연산 결과 42, 메모리 한도 1024 MiB |
| HAMi 메모리 제한 | 1536 MiB 할당을 OOM으로 거부, 테스트 exit code 0 |
| KubeVirt 1.9.0 | Deployed, 기본 Ubuntu VM Running/Ready |
| 기본 VM 내부 | SSH 접속, Ubuntu 22.04.5, cloud-init 완료 확인 |
| FLYT review | 실제 클러스터 검증 8개 통과 |
| FLYT CPU 검사 | 기존 단위 테스트 10개, 큐 왕복 1000회 통과 |
| FLYT 권한 수정 | 수정 전 오류 재현, 수정 후 회귀 검사 6개 통과 |
| FLYT Worker | 수정 이미지로 Ready 및 Attachment `Mapped` 확인 |
| FLYT Guest SHM | QEMU 장치 미지원으로 실행 불가; 성공으로 기록하지 않음 |

HAMi의 `nvidia.com/gpu=40`은 물리 GPU 40개가 아니라 GPU 4개에 대한
논리적 할당 슬롯이다. SM 25% 설정의 전달은 확인했으나, 처리량 측정으로
정확한 연산 비율까지 검증한 것은 아니다.

## 최종 구성

- GPU Operator: 기존 드라이버 유지, Toolkit/모니터링 활성화,
  자체 NVIDIA Device Plugin과 MIG Manager 비활성화, CDI 활성화.
- HAMi: `runtimeClassName: nvidia`, `deviceListStrategy: cdi-annotations`,
  호스트 드라이버 경로 `/`, 훅 경로 `/usr/local/nvidia/toolkit/nvidia-ctk`.
- HAMi가 `/var/run/cdi/k8s.device-plugin.nvidia.com-gpu.json`에 개별 GPU 장치를
  생성하고, GPU Operator의 관리용 CDI와 구분한다.
- KubeVirt: Sidecar 기능 활성화. GPU 직접 패스스루는 구성하지 않았다.
- FLYT: `flyt-review-validation`과 `flyt-gpu-validation` namespace 분리.

실제 적용 값은 [GPU Operator values](gpu-operator-values.yaml)와
[HAMi values](hami-values.yaml)에 저장했다. HAMi의 CDI 설정은
[공식 문제 해결 문서](https://project-hami.io/docs/next/troubleshooting)의
호스트 드라이버 구성에 맞췄다.

## 발생한 문제와 조치

### 1. 설치된 CRI-O와 실행 중인 CRI-O가 달랐음 — 해결

패키지와 `/usr/bin/crio`는 1.37.0인데 systemd가 `/usr/local/bin/crio` 1.35.0을
실행하고 있었다. [수정 스크립트](fix-crio-service.sh)를 준비하고 사용자가 root로
실행했다. 설정 백업 후 systemd drop-in으로 패키지 바이너리를 선택했다.
노드가 보고하는 `cri-o://1.37.0`과 API 복구를 확인했다.

버전 전환 중 Pod 샌드박스 재생성, API 중단 및 일시적 NotReady가 발생했다.
Toolkit의 설정 반영 재시작도 있었다. 이후 설치에서는 런타임 정합성과 복구를
먼저 확인한 다음 Helm 설치를 진행해야 한다.

### 2. 이미지 인증 및 digest 불일치 — 해결

기존 GHCR 이미지의 익명 접근이 거부되어 현재 소스를 rootless Podman으로
빌드했다. 사용자가 [이미지 import 스크립트](import-local-images.sh)를 실행했다.
OCI export 과정에서 manifest digest가 달라져, 빌드 시 digest 대신 실제
OCI archive/import된 digest를 배포에 사용했다. 외부 레지스트리로 push하지 않았다.

### 3. 중단된 Helm 설치가 pending-install로 남음 — 해결

런타임 전환 중 Helm이 일시적 API 오류로 종료되었으나 릴리스 상태는
`pending-install`로 남았다. 실행 중인 설치 프로세스가 없음을 확인하고
아직 GPU 작업이 없던 미완료 HAMi 및 active FLYT 릴리스만 제거·재설치했다.
정상 review 릴리스, CRD, TLS Secret, PV/PVC는 유지했다.

### 4. 관리용 CDI와 HAMi UUID 할당 충돌 — 해결

`management.nvidia.com/gpu=GPU-...`를 찾지 못하는 오류가 발생했다.
관리용 CDI에는 `all`만 있었지만 HAMi의 envvar 방식은 GPU UUID를 전달했다.
CDI 전역 비활성화와 legacy runtime도 확인했으나 이 노드에서는 NVIDIA 훅
오류가 재현되었다. legacy 경로의 훅 내부 오류 원인은 별도로 확정하지 않았다.

최종적으로 HAMi를 공식 지원하는 `cdi-annotations` 방식으로 구성했다.
개별 GPU CDI 파일 생성, 실제 CUDA 연산, 메모리 제한 검증까지 통과했다.
검증 로그의 `HAMI-core ... OOM`은 의도한 초과 할당 차단 결과다.

### 5. fsGroup 적용 후 Worker가 layout.bin을 거부 — 수정 및 검증

프로비저너는 `layout.bin`을 0640으로 생성했지만, Kubernetes가 PVC의 fsGroup을
적용하면서 0660으로 바꿨다. 기존 코드가 모든 group-write 파일을 거부해
Worker가 GPU 세션을 시작하기 전에 종료됐다.

[mapping.c](../../runtime/shm/src/mapping.c)는 **파일 소유자와 그룹이 모두
프로세스의 effective UID/GID와 일치할 때만** group-write를 허용하도록 수정했다.
world-write, 다른 소유자/그룹의 group-write, 심볼릭 링크는 계속 거부한다.
이는 VM과 Worker의 할당 UID/GID를 신뢰하는 전용 namespace/PVC 구성을 전제로 한다.

[회귀 검사](../../tests/integration/layout_permissions.py) 6개가 통과했고,
수정 Worker 이미지의 실제 Ready/`Mapped` 상태도 확인했다.
사용자가 [수정 이미지 import](import-worker-fix.sh)를 실행했다.

### 6. 공식 QEMU의 ivshmem 장치 미지원 — 미해결, 추가 구성 필요

기본 VM은 정상 부팅하지만 FLYT 훅이 추가한 장치를 만들 때 다음 오류가 발생한다.

```text
ivshmem-plain is not a valid device model name
```

사용 중인 QEMU는 `10.1.0 (qemu-kvm-10.1.0-20.el9)`이며 실제 `-device help`에도
해당 장치가 없다. `Sidecar` 활성화나 Kubernetes 설정 변경만으로 해결되지 않는다.
ivshmem을 포함하는 커스텀 QEMU/virt-launcher의 빌드·배포·호환성 검증이 필요하다.
Guest 테스트 프로그램은 준비했으나 VM 내부 SHM 왕복 검사는 실행하지 못했다.

### 7. 실패한 채널의 종료 증거 유실 — 안전하게 보존, 개선 필요

채널 a는 Worker의 `Detached`를 확인했으나, launcher Pod가 제거되기 전에
Guest terminal 상태를 관측하지 못했다. 따라서 `Draining/AwaitingDetachEvidence`와
약 64 MiB backing을 보존했다. Pod가 없다는 이유로 detach를 추정하거나
Channel finalizer를 강제로 제거하지 않았다.

채널 b에서는 테스트용 finalizer로 launcher의 종료 상태를 일시 보존했다.
실제 모든 컨테이너 종료와 노드 Ready를 컨트롤러가 관측한 후, 양쪽 `Detached`,
reclaim Pod 완료, Channel `Released`를 확인했다. 그 다음 테스트용 finalizer만
제거했다. 이 검사는 **종료 증거 보존을 보조한 조건부 회수 검증**이다.
현재 컨트롤러의 polling만으로 종료 증거를 항상 보존한다고 판단할 수 없다.

## 남겨 둔 자원과 로그

- 기본 VM `flyt-infra-validation/basic-vm`: Running, 재접속·확인용.
- FLYT review/active 컨트롤러와 웹훅: Running.
- 채널 a: Draining, 파일 및 finalizer 보존, VM 정지·Worker 제거됨.
- 채널 b: Released, backing 회수 완료, VM 정지·Worker 제거됨.
- local PV a/b: Retain 정책. 실증 디렉터리는 `/var/lib/flyt-poc-20260921/`.
- 원시 증거: `.local/gpu-validation-20260921/` (Git 제외).

주요 증거 파일은 `hami-smoke-cdi.log`, `basic-vm-ssh.log`, `review-smoke.json`,
`layout-before.log`, `layout-after.log`, `qemu-supported-devices.txt`,
`active-events.txt`, `shm-b-launcher-terminal.json`, `shm-b-released.json`이다.
이 경로에는 테스트 SSH/TLS 키도 있으므로 통째로 공유하거나 커밋하지 않는다.

Kubernetes 1.37 조합과 이 저장소의 active 경로는 실험적 검증이다.
기본 구성 요소 설치 성공을 FLYT 전체 기능이나 운영 환경 검증 성공으로 확대하지 않는다.
