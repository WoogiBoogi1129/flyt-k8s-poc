# 10-09 SHM lifecycle — source authored, NOT_RUN

Guest I/O thread가 idle일 때 2초 간격 heartbeat를 전송한다. Worker는 HELLO 전 600초,
HELLO 후 요청이 없는 60초를 지나면 해당 session process를 종료한다. CUDA 호출 내부에서
블록된 경우 watchdog이 선점하지 못하며 Guest deadline은 별도로 실패 처리한다.
timeout 뒤 요청 재실행·slot 재시작·같은 generation 재사용은 없다. Guest 정상 종료는 GOODBYE,
비정상 종료는 heartbeat 중단으로 처리한다. supervisor는 실패한 slot만 실패 상태로 두고
나머지 client를 재시작하지 않는다. 초기 slot들이 모두 연결돼야 Channel Ready가 된다.

Worker supervisor가 직접 자식 실행 프로세스의 Queue open/guard/HELLO marker를 읽어 attachment
Mapped를 보고한다. 종료는 모든 child wait 이후 Worker Detached를 보고한다. 상태 접근은
해당 Channel/Attachment 이름으로 한정된 ServiceAccount/Role을 생성한다.
CUDA 실행 프로세스가 아니라 supervisor가 Kubernetes control API를 호출한다.

Guest/QEMU detach는 VMI UID에 해당하는 launcher Pod UID를 먼저 보존하고 동일 Pod의 terminal
상태·모든 container 종료·Node Ready를 확인한다. Worker terminal Pod도 동일 UID로 관찰한다.
missing/force-deleted Pod, unreachable node는 해제 증거로 취급하지 않는다. 증거를 놓쳤거나
pre-bind 실패한 채널은 finalizer가 남을 수 있으며 별도 fencing/운영자 복구가 필요하다.
자동 강제 finalizer 제거는 제공하지 않는다.

두 attachment detach와 generation을 확인한 뒤 동일 노드 reclaim Pod가 allocation의 세 파일만
지운다. PVC 자체·다른 allocation·다른 GPU를 삭제하지 않는다. 파일/세대 불일치는 거절하며
재귀 삭제는 하지 않는다. 정리 중 장애가 나면 부분 상태를 유지하고 finalizer를 보존한다.
새 요청은 새 allocation/generation을 사용한다. Manager RAM 상태로 채널을 재구성하지 않는다.

현재 Pod UID, Channel UID/generation, VMI UID가 실행 세대 역할을 한다. 기존 7단계 Manager epoch
protocol을 재사용하지 않는다. JSON endpoint 관리층은 Channel/Attachment CRD로 대체한다.
큰 CUDA 호출의 강제 취소, checkpoint/live migration, transparent recovery는 지원하지 않는다.
모든 정적 검사·빌드·프로토콜/장애 시험·배포·VM/GPU 실행은 NOT_RUN이다.
