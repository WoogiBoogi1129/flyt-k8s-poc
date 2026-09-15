# FLYT SHM-only Kubernetes / KubeVirt development

현재 브랜치는 `stage/10-10-rpc-removal`이다. **SHM 경로 소스 작성·모든 검증 NOT_RUN** 상태이며,
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
Runtime fatbinary 등록·전체 Graph 연산·cuDNN 연산·기타 라이브러리·unmodified PyTorch는 미지원이다.
[지원 표](experiments/shm-compatibility/README.md)와 [남은 작업](experiments/rpc-removal/README.md)을 확인한다.
과거 MPS의 PyTorch 결과를 SHM 검증 결과로 사용할 수 없다.

## 향후 빌드·배포 준비

아래는 실행하지 않은 후속 절차다. GPU 설정과 기존 Kubernetes 설치를 이 작업에서 변경하지 않았다.

1. 개발물 검증을 재개할 때 CMake/이미지 빌드와 CPU Queue 시험부터 수행한다.
2. `images/flyt/Containerfile`의 `control-plane`, `worker`, `guest-artifacts`를 각각 빌드한다.
   hook은 별도 `Hook.Containerfile`과 실제 설치 버전의 digest-pinned Sidecar-shim image가 필요하다.
3. 별도 SHM 실험 클러스터/namespace에 Profile/Request 및 Channel/Attachment CRD를 설치한다.
   `deploy/shm`의 CRD는 SHM 전용이며 기존 RPC 클러스터 CRD를 무조건 덮어쓰면 안 된다.
4. admission TLS Secret과 CA를 준비하고 `scripts/render-shm.py`로 manifests를 만든다.
   이 renderer는 출력만 하며 apply하지 않는다. webhook과 전용 namespace가 함께 있어야 시작 gate가 작동한다.
5. 승인 GPU Profile, 정지 VM, 같은 노드 local filesystem PVC, Request와 Channel을 준비한다.
   BackingReady 이후 VM을 수동 시작한다. Guest에 layout.bin을 전달하고 명시적 ivshmem BDF/slot을 지정한다.
6. 양쪽 mapping ACK와 Ready를 확인한 뒤 지원 API를 실행·검증한다. 종료는 Channel drain 절차를 따른다.

일반 GPU 없는 노드에서는 control plane/Queue 검증까지 가능하지만 실제 CUDA Worker 실행은 별개다.
KubeVirt hook/PVC 공유·Guest BAR mapping 속성과 HAMi 실행은 아직 실제 환경에서 확인하지 않았다.
검증을 생략한 소스 개발만으로 배포 가능/운영 준비 완료라고 판단하지 않는다.

[단계별 기록](docs/DEVELOPMENT_STAGES.md) · [최종 개발 상태](experiments/rpc-removal/README.md)
