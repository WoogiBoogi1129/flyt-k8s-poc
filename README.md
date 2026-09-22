# FLYT SHM-only Kubernetes / KubeVirt development

CPU 기반 Kubernetes 배포는 [Stage 1 설치·운영 안내](docs/CONTROL_PLANE_STAGE1.md)를 참고하세요. `review` 모드의 실클러스터 검증과 GPU 런타임 검증 범위는 별개입니다.

현재 브랜치는 `feat/k8s-native-control-plane`이다. **CPU review control plane은 실클러스터 검증 완료**,
실제 VM의 SHM GPU 실행 및 고정 FP32 eager MLP·SGD를 검증했으며,
전체 CUDA/PyTorch 호환성은 미완료다. 이전 RPC 구현은 `legacy/rpc`와 1~7단계 브랜치에 보존했다.
기본 Makefile, 이미지, 배포 진입점은 RPC 서버·Manager·rpcbind·libtirpc를 사용하지 않는다.

## 현재 구조

VM의 CUDA interception → per-session Request/Response Ring + payload → Worker dispatcher →
HAMi/CUDA 순서다. `runtime/shm`에 mapping, CUDA adapter, Guest library, Worker 및 관리 코드를 둔다.
`FlytSharedMemoryChannel`은 allocation과 VM/Worker 배치를, `FlytChannelAttachment`는
매핑·해제 근거를 관리한다. Kubernetes API와 KubeVirt hook 관리 통신은 계속 사용한다.

## 지원 범위

기본 메모리/device, stream/event, 제한된 async 복사, 명시적 ABI의 PTX Driver launch,
빈 노드 Graph lifecycle, cuBLAS float SGEMM, cuDNN handle/version 소스를 작성했다.
Runtime fatbinary 등록·packed kernel 인자 전달을 추가했고, 고정 PyTorch 빌드의 대표 학습을
passthrough VM과 비교했다. 전체 Graph·cuDNN 연산·기타 라이브러리와 임의 PyTorch 호환성은 미지원이다.
현재 지원 계약은 [SHM API 범위](runtime/shm/API_SUPPORT.md), 실제 학습 증거는
[PyTorch 구현·검증 보고서](experiments/evidence/PYTORCH_IMPLEMENTATION_2026-09-22.md)를 따른다.
[지원 표](experiments/shm-compatibility/README.md)와 [남은 작업](experiments/rpc-removal/README.md)을 확인한다.
과거 MPS의 PyTorch 결과를 SHM 검증 결과로 사용할 수 없다.

## GPU PoC 후속 빌드·배포 준비

아래는 GPU 런타임 준비 순서다. 실제 개발 검증 재현은
[VM 준비](experiments/evidence/REPRODUCE_VM_DEVELOPMENT.md)와
[PyTorch 실행](experiments/evidence/REPRODUCE_PYTORCH.md)을 참고한다. CPU 배포 결과는 [Stage 1 검증 기록](docs/CONTROL_PLANE_VALIDATION.md)을 참고한다.

1. 개발물 검증을 재개할 때 CMake/이미지 빌드와 CPU Queue 시험부터 수행한다.
2. control plane은 `images/flyt/ControlPlane.Containerfile`, `worker`와 `guest-artifacts`는 기존 `images/flyt/Containerfile`로 빌드한다.
   hook은 별도 `Hook.Containerfile`과 실제 설치 버전의 digest-pinned Sidecar-shim image가 필요하다.
3. 별도 SHM 실험 클러스터/namespace에 Profile/Request 및 Channel/Attachment CRD를 설치한다.
   `deploy/shm`의 CRD는 SHM 전용이며 기존 RPC 클러스터 CRD를 무조건 덮어쓰면 안 된다.
4. admission TLS Secret과 CA를 준비하고 Helm chart를 별도 PoC namespace에 설치한다.
   GPU 실행에는 experimental active 모드 설정과 확대된 권한 검토가 필요하다.
5. 승인 GPU Profile, 정지 VM, 같은 노드 local filesystem PVC, Request와 Channel을 준비한다.
   BackingReady 이후 VM을 수동 시작한다. Guest에 layout.bin을 전달하고 명시적 ivshmem BDF/slot을 지정한다.
6. 양쪽 mapping ACK와 Ready를 확인한 뒤 지원 API를 실행·검증한다. 종료는 Channel drain 절차를 따른다.

일반 GPU 없는 노드에서는 control plane/Queue 검증까지 가능하지만 실제 CUDA Worker 실행은 별개다.
KubeVirt hook/PVC 공유·Guest BAR mapping과 HAMi 실행은 gpu-4에서 확인했다.
RPC·MPS를 포함한 정식 성능 비교와 전체 장애 시험은 아직 완료되지 않았다.
검증을 생략한 소스 개발만으로 배포 가능/운영 준비 완료라고 판단하지 않는다.

[단계별 기록](docs/DEVELOPMENT_STAGES.md) · [최종 개발 상태](experiments/rpc-removal/README.md)
