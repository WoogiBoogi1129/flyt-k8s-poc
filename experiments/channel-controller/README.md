# 10-05 channel controller — source authored, NOT_RUN

별도 `runtime/shm/control/channel_controller.py`와 admission 서버를 작성했다.
기존 Go/RPC Controller를 바꾸지 않고 전용 namespace에서 SHM Controller만 실행한다.
`FlytGPURequest`, `FlytGPUProfile`은 기존 CRD를 참조한다. 새로운 `FlytSharedMemoryChannel`은
allocation·준비 Pod·VM template·Worker Pod를, `FlytChannelAttachment`는 holder UID별
mapping/detach 근거를 관리한다. `crds.json`은 설치용 CRD 소스이며 적용/검증하지 않았다.

초기 VM은 Halted여야 한다. Channel 생성 → 전용 PVC 준비 Pod → BackingReady → 사용자 VM 시작
→ VMI UID 고정 → HAMi Worker 생성 → 양쪽 mapping ACK → Ready 순서다. 자동 VM 시작은 하지 않는다.
VM template은 승인 node와 PVC hook으로 갱신하며 기존 RPC opt-in annotation을 제거한다.
한 allocation의 VM/Request/PVC와 images는 불변이다. Request/Profile generation 변경은 실패·drain이다.
모든 image는 digest를 요구한다. PVC는 운영자가 준비한 동일 노드의 local filesystem PVC여야 한다.

Admission webhook은 전용 namespace VMI CREATE를 BackingReady/VM UID/세대/배치/hook으로 검증하고
SHM VMI의 migration을 거절한다. TLS Secret/CA bundle과 `failurePolicy: Fail`, namespaceSelector를
설치할 때 함께 구성해야 한다. webhook 미설치 상태는 시작 gate가 완성된 배포가 아니다.
HTTP 서버는 관리 연결이며 CUDA data path는 SHM 전용이다. KubeVirt 자체 hook gRPC도 제거 대상이 아니다.

Channel status 변경 권한은 controller, Attachment status는 신뢰된 observer에만 부여한다.
일반 Guest/사용자에게 status 쓰기 권한을 주지 않는다. observer 연결·실제 detach 확인은 10-09 범위다.
삭제/drain은 해당 VM을 Halted로 바꾸고 해당 Worker만 종료한다. missing Pod는 detach 증거가 아니다.
근거가 없으면 finalizer를 유지한다. PVC 자체는 자동 삭제하지 않으며 allocation 파일 회수는 후속 단계다.
초기화 실패 allocation도 재초기화하지 않는다. 검증 전 운영 적용을 권장하는 의미가 아니다.

Controller는 1 replica/Recreate 및 resourceVersion 낙관적 갱신을 전제로 한다. 두 namespace 이상 또는
복수 leader 실행은 현재 지원하지 않는다. API 오류는 보수적으로 실패 처리한다.
10-06~10-10에서 실행 파일·세션 observer·SHM 전용 이미지/배포 구성을 연결한다.
빌드, 정적 검사, CRD/admission 시험, 배포, VM/GPU 실행은 모두 NOT_RUN이다.
