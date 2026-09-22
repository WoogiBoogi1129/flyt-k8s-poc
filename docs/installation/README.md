# gpu-4 설치 및 실증 기록

실행 대상은 `feat/k8s-native-control-plane` 브랜치의
`c4b67fc812b01c076bdca5b5e2003fc2d66b5f0a`이다.
사용자가 이미 완료한 2단계(control-plane taint 제거)는 다시 실행하지 않았다.

상세 장애 원인, 조치, 검증 결과는
[한국어 실행 보고서](INSTALLATION_REPORT_2026-09-22.md)에 기록한다.
설치 성공과 실제 GPU/VM/SHM 동작 성공은 별도로 판정한다.

## 적용한 구성

| 구성 요소 | 버전 및 설정 |
| --- | --- |
| Kubernetes / CRI-O | 1.37.0 / 1.37.0 |
| NVIDIA GPU Operator | 26.7.0, 기존 드라이버 유지, Toolkit 설치 |
| HAMi | 2.10.0, NVIDIA 자원 등록 담당, `nvidia` RuntimeClass |
| KubeVirt | 1.9.0, `Sidecar` 기능 활성화 |
| FLYT | review와 active를 서로 다른 namespace에 배포 |

KubeVirt 1.9의 지원 범위를 벗어나는 Kubernetes 1.37 조합은 이 노드의
실험 결과로만 판단한다. 단일 노드 실증 결과가 다른 노드, HA, 운영 환경의
호환성을 보장하지 않는다.

GPU Operator의 Device Plugin과 MIG Manager는 비활성화했고 CDI는 활성화했다.
HAMi가 GPU 자원을 등록하고, KubeVirt VM은 GPU를 직접 패스스루하지 않고
FLYT Worker와 SHM으로 통신하는 구성이다.
HAMi는 `cdi-annotations`로 개별 GPU CDI 명세를 생성한다.
관리용 CDI/개별 GPU UUID 충돌을 해결한 최종 설정이며,
`nvidia` RuntimeClass와 호스트 드라이버 경로 `/`를 명시한다.

## 동일 구성의 설치 순서

1. CRI-O의 실행 바이너리와 Kubernetes 버전을 맞추고 노드/API 복구를 기다린다.
2. 이 디렉터리의 values로 GPU Operator를 설치하고 Toolkit과 CUDA validator를 확인한다.
3. HAMi를 설치하고 스케줄러/Device Plugin 준비 및 실제 메모리 제한을 검증한다.
4. KubeVirt operator와 CR을 적용하고 Sidecar 기능, 기본 VM 부팅을 확인한다.
5. 로컬 이미지를 import한 뒤 FLYT review 검증, 별도 namespace의 active 검증을 진행한다.

설치 중 CRI-O를 변경하면 진행 중인 Helm 작업이 중단될 수 있다.
다음은 이번 실행에서 최종 사용한 Helm 명령이다.

```sh
helm upgrade --install gpu-operator gpu-operator \
  --repo https://helm.ngc.nvidia.com/nvidia \
  --namespace gpu-operator --create-namespace --version v26.7.0 \
  -f docs/installation/gpu-operator-values.yaml --wait --timeout 12m

helm upgrade --install hami hami \
  --repo https://project-hami.github.io/HAMi/ \
  --namespace kube-system --version 2.10.0 \
  -f docs/installation/hami-values.yaml --wait --timeout 15m
```

Helm의 `deployed`만으로 Operator가 관리하는 모든 구성 요소의 준비가
보장되지는 않는다. 아래 상태 확인과 실제 실행 검증이 별도로 필요하다.

## 파일 안내

- `gpu-operator-values.yaml`, `hami-values.yaml`: 실제 Helm 설정.
- `fix-crio-service.sh`: 잘못 선택된 CRI-O 바이너리를 패키지 버전으로 전환.
  사용자가 root로 실행 완료했다. 설정 백업과 실패 시 서비스 복원 기능이 있다.
  런타임 버전 변경 시 Pod/API 중단이 발생할 수 있으며 재실행용 스크립트가 아니다.
- `import-local-images.sh`: 로컬 OCI 이미지 로드 및 실증 전용 디렉터리 생성.
  사용자가 root로 실행 완료했다.
- `hami-smoke.cu`: 실제 CUDA 연산과 1024 MiB 메모리 제한 검증.
- `queue-smoke.c`: CPU 프로세스 간 SHM 큐 검증. 실제 VM BAR 검증과 구분한다.
- `guest-smoke.c`: Guest → SHM → GPU Worker 메모리 전송 검증 프로그램.
  QEMU ivshmem 미지원으로 실제 Guest 실행은 미완료다.
- `import-worker-fix.sh`: fsGroup 권한 검사 수정 Worker 이미지 로드. 실행 완료.

실행 로그, digest, VM/Pod/Channel manifest 및 빌드 결과는 Git에서 제외한
`.local/gpu-validation-20260921/`에 저장한다. SSH 키와 테스트 TLS 키도 이 경로에
있으므로 이 디렉터리를 통째로 Git에 추가하거나 외부에 공유하지 않는다.

## 상태 확인

```sh
kubectl get nodes -o wide
helm list -A
kubectl get clusterpolicy
kubectl get kubevirt -n kubevirt
kubectl get pods -n gpu-operator
kubectl get pods -n kube-system -l app.kubernetes.io/instance=hami
kubectl get vm,vmi,pods -n flyt-infra-validation
kubectl get flytsharedmemorychannels,flytchannelattachments,pods -n flyt-gpu-validation
```

실증 데이터의 local PV는 `Retain` 정책이다. 종료할 때는 Channel의 drain과
Guest/Worker의 실제 detach, backing 회수 결과를 먼저 확인한다. 파일 삭제나
finalizer 강제 제거로 종료 검증을 대신하지 않는다.
