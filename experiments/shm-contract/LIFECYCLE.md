# 배치·채널 수명주기 계약

## 책임과 키

- VM Lifecycle Manager: VM 시작 요청과 시작 허용 여부를 관리한다.
- GPU Resource Orchestrator: 요청 snapshot, allocation, VMI/Worker/channel binding을 관리한다.
- Placement: VM과 Worker가 배치될 같은 노드를 결정한다. 초기에는 승인 Profile의 node/UUID를 사용한다.
- Kubernetes/HAMi: 실제 Worker GPU 할당 및 quota 적용을 담당한다. 이 단계에서 다중 GPU 자동 선택은 추가하지 않는다.
- 향후 `FlytSharedMemoryChannel`: backing·mapping·generation과 회수 상태를 CRD 단위로 관리한다.

VMI 생성 전에는 VM UID + Request UID/generation에 연결된 난수 allocation ID를 발급한다.
allocation은 하나의 VM 시작 시도에만 사용한다. VM UID만으로 backing 경로를 재사용하지 않는다.
VMI UID는 생성 이후 확정하여 한 번만 bind한다. 다른 VMI로 재bind하지 않는다.

## 시작 순서

1. 중지된 VM의 시작 요청을 접수한다. 임의의 기존 실행 VM에 SHM 장치를 뒤늦게 붙이지 않는다.
2. Request/Profile/ControlPlane UID와 quota를 snapshot하고 대상 node를 결정한다.
3. allocation/channel generation을 발급하고 `Reserved` 상태를 만든다.
4. node-local backing과 Guest 장치 attachment 정보를 준비하여 `BackingReady`로 전환한다.
5. VM template에 allocation 식별자·장치 연결·동일 노드 배치 조건을 연결한 후 시작을 허용한다.
6. 생성된 VMI의 owner VM UID·allocation·배치 노드를 확인하고 해당 VMI UID를 고정한다.
7. 기존 Worker 생성 흐름을 확장해 같은 allocation과 node로 Worker를 생성한다.
8. Worker GPU 할당, VMI/Worker/Pod identity를 확인해 `Bound`로 전환한다.
9. 양쪽 mapping 및 ABI/generation handshake를 확인하고 `Ready`에서 세션을 연다.

VM을 먼저 시작한 뒤 Controller가 모든 준비를 따라잡을 것이라고 가정하지 않는다. 10-05에서
명시적인 시작 경로 또는 admission/start gate를 구현해야 한다. KubeVirt API 바깥에서 VM을
시작하는 요청 역시 이 gate를 우회하면 채널 Ready를 얻을 수 없어야 한다.

BackingReady는 GPU 예약 성공이 아니다. 최종 HAMi 할당이 실패하면 Worker/VM 시작 절차를
중단하고 해당 allocation을 회수한다. GPU가 없는 노드로 자동 재배치하거나 원격 RPC를 사용하지 않는다.

## 종료·실패·재시작

`Reserved → BackingReady → Bound → Ready → Draining → Released`를 정상 흐름으로 한다.
각 준비 단계 실패는 `Failed`에 원인을 남기고 `Draining`으로 회수한다. 실패 상태를 Ready로
강제 변경하지 않는다. 다음 시작 시도는 새 allocation/generation을 사용한다.

Draining 순서:

1. 신규 세션·요청 접수 차단
2. 세션 종료 및 Worker 실행 프로세스 정리
3. Guest/QEMU attachment와 Worker mapping 해제 확인
4. backing 삭제 및 노드 측 자원 회수 확인
5. Released 기록 후 finalizer 제거

Worker 종료만으로 QEMU가 backing을 놓았다고 판단하지 않는다. Node unreachable이나 회수
불확실 상태에서는 finalizer와 식별 정보를 유지하고 이름 재사용을 금지한다. timeout만으로
파일을 지우거나 old backing을 새 VMI에 넘기지 않는다. 관리자 복구는 별도 절차로 정의한다.

초기 구현에서는 active SHM channel의 live migration을 지원하지 않는다. 동일 VM의 새 VMI,
Worker generation 변경, backing 교체가 있으면 기존 CUDA 세션을 복원하지 않는다. 채널 변경은
drain을 거쳐 새 generation으로 연결하고, 기존 요청에 대한 늦은 응답을 거절한다.

Manager 재시작으로 epoch가 바뀌면 기존 세션은 종료 대상이다. Controller는 CRD와 노드의
실제 allocation 상태를 대조해 복구하며, 메모리에 세션 레코드가 없다는 이유만으로 backing을
삭제하지 않는다. 이전 세대가 종료됐다는 증거 없이 새 Ready를 게시하지 않는다.

## 장치 adapter 전제

ivshmem은 첫 연결 후보이며 현재 KubeVirt 환경에서 사용 가능하다고 검증하지 않았다.
10-04는 QEMU attachment, Guest mapping, Worker mapping, volume/권한 및 notification을
함께 구현한다. Hook/XML 수정만으로 동일 backing 접근이 성립했다고 판정하지 않는다.
node-local backing provider는 원시 host path를 사용자 입력으로 받지 않고 allocation으로
식별한다. 기능 사용에 필요한 플랫폼 확장은 검증 전 별도로 명시한다.
