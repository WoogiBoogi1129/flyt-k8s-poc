# SHM ABI 1.0 초안

10-01에서 고정하는 후속 구현 계약이다. 헤더·JSON Schema는 미컴파일/미검증이며 실제 runtime에
연결하지 않았다. ABI 변경은 구현 중 별도 커밋과 버전 변경으로 추적한다.

## 채널과 메모리 배치

VMI별 backing을 분리한다. 하나의 VM에 속한 CUDA client들은 이 backing 안에서 세션별
request ring, response ring, request payload arena, response payload arena를 배정받는다.
같은 VM 안의 세션 분리는 논리적 분리이며 악성 guest 프로세스 간 하드웨어 격리를 제공하지 않는다.
서로 다른 VMI에는 같은 backing을 매핑하지 않는다.

ABI 1.0의 초기 지원 대상은 동일 노드의 little-endian x86-64 Guest/Host다. Queue 구현은
공유 영역에서 사용할 정렬된 64-bit atomic의 lock-free 동작과 메모리 매핑 속성을 확인해야 한다.
이 조건을 만족하지 않으면 연결을 거절한다. 일반 process-shared mutex나 futex가 VM 경계를
그대로 넘는다고 가정하지 않는다. CPU 이외 아키텍처 지원은 별도 ABI 검토 대상이다.

첫 4096 bytes는 채널 header 예약 영역이다. Guest에 전달하는 backing 전체 크기를 초과하는
영역이나 영역 간 overlap은 허용하지 않는다. Host는 control plane에서 받은 layout을 private
memory에 보관하며 guest가 수정 가능한 공유 header를 권한의 근거로 사용하지 않는다.

| Header offset | 형식 | 내용 |
|---|---|---|
| 0 | 8 bytes | ASCII `FLYTCHN1` |
| 8 / 10 | u16 / u16 | major=1, minor=0 |
| 12 | u32 | header bytes=4096 |
| 16 | 16 bytes | allocation ID |
| 32 | 16 bytes | channel generation |
| 48 | u64 | region bytes |
| 56 | u32 | session limit, 1..32 |
| 60..4095 | bytes | zero, reserved |

각 ring은 128-byte control 영역과 `entries * 128` bytes의 descriptor array다.
control offset 0에는 producer의 u64 tail, offset 64에는 consumer의 u64 head를 두고 나머지는
0으로 둔다. 인덱스는 0부터 증가한다. wrap 직전 채널/세션을 drain하고 새 generation을 만든다.
counter를 같은 generation에서 0으로 돌리거나 overflow를 허용하지 않는다.

세션 endpoint JSON은 offset·크기와 identity를 제공한다. ring offset은 backing 시작 기준이며
payload descriptor의 offset은 해당 방향의 세션 payload arena 시작 기준이다.
JSON Schema만으로 영역 overlap, 덧셈 overflow, ring/control 영역 크기 또는 실제 backing
범위를 검증할 수 없다. 10-03 구현은 이를 별도로 검사해야 한다.

## Queue 소유권과 순서

세션마다 논리적 SPSC request/response ring 한 쌍을 사용한다. 초기 Guest client는 동일
세션의 여러 host thread를 로컬에서 직렬화하며 동시에 게시하는 API 요청은 하나로 제한한다.
이는 초기 CPU 호출의 병렬성을 제한하지만 서로 다른 CUDA stream의 GPU 실행을 매 호출마다
device synchronize한다는 뜻은 아니다. 병렬 제출 확장은 별도 ABI/상태 검토 후 수행한다.

1. Producer가 arena에 payload와 descriptor를 작성한다.
2. Producer가 tail을 release-store하여 게시한다.
3. Consumer가 tail을 acquire-load하여 확인한다.
4. Consumer가 descriptor를 private memory로 복사한 뒤 identity·범위·API schema를 검사한다.
5. Consumer가 필요한 입력 payload를 private 실행 버퍼로 복사하여 검증한다.
6. Consumer가 head를 release-store하여 ring slot을 반환한다.

Producer는 head를 acquire-load하여 남은 slot을 확인한다. `head <= tail`과 `tail-head <= entries`
조건을 위반하면 세션을 실패 처리한다. Guest가 descriptor/payload를 동시에 변경할 수 있으므로
CUDA 처리에서는 shared memory를 반복해서 검증 없이 재참조하지 않는다. 입력 데이터의 의미적
일관성은 client 책임이지만, 크기·주소·handle 검사는 복사된 입력에 대해 수행해야 한다.

ring slot 반환은 payload 재사용 허가와 같지 않다. 요청 payload는 응답 수신까지 유지한다.
응답 payload는 Guest가 내용을 복사한 뒤 response head를 진행할 때 회수할 수 있다.
초기 one-in-flight 계약에서 양쪽 arena는 해당 요청/응답이 끝나기 전 덮어쓰지 않는다.
arena보다 큰 payload는 명시적인 크기 오류로 거절한다. 큰 memcpy의 chunking과 비동기 staging
수명 관리는 10-06/07에서 API schema와 함께 추가한다.

## Descriptor와 API 처리

128-byte descriptor의 정확한 offset은 `include/flyt_shm_contract.h`를 기준으로 한다.
정수는 little-endian으로 개별 encode/decode한다. 구조체 padding·native enum·주소값을
그대로 공유하지 않는다. magic/version, kind, zero flags/reserved, channel generation,
session ID, request ID, API ID와 payload schema를 모두 검사한다.

Request ID는 세션마다 1부터 증가하며 재사용하지 않는다. Consumer는 초기 직렬 요청에서
정확히 다음 ID만 허용한다. 응답의 identity·ID·API·schema는 요청과 일치해야 한다.
미지원 API/schema는 `UNSUPPORTED_API`로 반환한다. 버전·identity·descriptor 오류는 CUDA를
실행하지 않고 세션을 거절/실패 처리한다. 손상된 ring에는 오류 응답 게시도 보장하지 않는다.

Transport status와 CUDA 반환값은 별개다. transport 성공일 때만 result domain/API result가
유효하다. Runtime/Driver/library 오류를 하나의 CUDA success 값으로 합치지 않는다. library
API는 API ID와 payload schema에서 반환값의 의미를 정의한다. 임의 enum cast로 번역하지 않는다.

`completion=API_RETURN`은 해당 CUDA API가 반환될 조건이 충족됐다는 뜻이다. 비동기 API에서
GPU 작업 완료를 뜻하지 않는다. Async H2D 등의 입력은 Host 소유 staging으로 넘겨 수명을
유지해야 한다. 초기 SHM buffer를 GPU pinned/zero-copy buffer로 간주하지 않는다.

Guest 가상 주소를 Host 포인터로 역참조하지 않는다. Device pointer와 CUDA handle은 세션별
mapping을 거치며 세션 간 값 재사용을 금지한다. Device pointer arithmetic, 배열·구조체·kernel
argument의 내부 포인터는 API별로 변환한다. API ID를 지정하는 것만으로 CUDA API가 구현되지는 않는다.

## 알림과 실패

첫 backend는 `polling-v1`이다. Request/response notification은 adapter 경계로 두고 실제
VM 장치 알림은 10-04에서 추가한다. 알림은 힌트이며 Queue 인덱스가 상태의 기준이다.
알림을 추가할 때는 인덱스 확인→알림 준비→재확인→대기의 순서로 lost wakeup을 처리해야 한다.

게시 전 queue full은 미제출이다. 게시 후 timeout/상대 종료는 실행 여부 불명일 수 있으므로
`EXECUTION_UNKNOWN`으로 처리하고 동일 요청을 자동 재전송하지 않는다. 새 request ID로 바꿔
재실행하는 것도 자동으로 하지 않는다. ABI 1.0에는 실행 중 CUDA 요청 취소·세션 복원 기능이 없다.

채널 실패 시 신규 제출을 중단하고 실행 프로세스·mapping 정리를 완료한 뒤 backing을 회수한다.
늦은 응답이나 옛 generation의 메시지는 폐기한다. RPC fallback은 어떤 오류에서도 사용하지 않는다.

## 제어 endpoint

새 Guest/Manager/Worker 제어 버전은 `flyt-shm-control-v1`로 구분한다. 7단계 JSON 세션 프로토콜을
조용히 재해석하지 않는다. 새로운 endpoint는 `schema/session.schema.json`에 따라 allocation,
channel generation, session, Manager epoch, VMI/Worker/Pod identity와 Queue layout을 전달한다.
RPC ID·RPC 주소 필드는 없다. UID들은 인증 비밀이 아니며 기존 접근 제어를 대신하지 않는다.

본 단계의 Schema는 데이터 계약이다. Kubernetes CRD나 실제 admission validation으로 설치하지
않는다. session layout과 채널 상태가 올바르더라도 Guest·Worker 양쪽 mapping ACK 전에는
CUDA 제출을 허용하지 않는다.
