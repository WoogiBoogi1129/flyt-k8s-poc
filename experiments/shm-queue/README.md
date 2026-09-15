# 10-03: SHM Ring Queue와 payload 전달

**소스 작성 완료 · 검증 NOT_RUN · VM/Guest/Worker runtime 미연결.**
10-02 커밋 `d47670f0effaa79f64254a74a1133adfaaec4a6e`에서
`stage/10-03-shm-queue`를 분기했다. 8·9단계와 CPU 배포 실험은 계속 보류한다.
이전 단계, 이미지, Controller, 외부 소스 checkout, GPU/노드 설정은 보존한다.

## 구현 범위

| 구성 | 구현 |
|---|---|
| Layout | 전체 세션의 ring/control/payload 범위, 정렬, overlap, overflow, ID 중복 검사 |
| Header | ABI 1.0의 4096-byte header 생성·snapshot·검사 |
| Descriptor | 128-byte little-endian encode/decode, reserved/status/kind 검사 |
| Ring | SPSC request/response, release/acquire u64, private shadow counter |
| Payload | private snapshot, arena 상대 offset/길이 검사, 결과 복사 후 slot 반환 |
| Guest | `flyt_shm_submit`, `flyt_shm_receive` 본체와 nonblocking try_receive |
| Worker | take → 외부 실행 adapter → respond, request snapshot release |
| Polling | CLOCK_MONOTONIC deadline, 1ms 간격 receive 대기, 명시적 abort |
| 빌드 구성 | CUDA/RPC 없는 별도 C11 static library; CMake 작성만 수행 |

코드는 [include/flyt_shm_queue.h](include/flyt_shm_queue.h)와
[src/queue.c](src/queue.c)에 있다. wire 규격은 [10-01 PROTOCOL](../shm-contract/PROTOCOL.md)을
따른다. 10-01의 `flyt_cuda_dispatch`는 아직 선언만 존재한다. 10-02 typed CUDA call로의
payload 변환과 API별 schema 검사는 10-06에 연결한다. Queue는 CUDA payload 의미를 해석하지 않는다.

## 제공자가 준비할 것

이 라이브러리는 이미 매핑된 주소와 권한 있는 control plane에서 받은 **전체 세션 layout**을
받는다. mmap/ivshmem/QEMU 장치, 권한, Manager epoch·UID 검증, mapping ACK, 실행 프로세스와
backing 회수는 10-04/05 및 후속 통합 작업이다. 공유 header를 승인 layout의 출처로 사용하지 않는다.

1. 제공자는 새 allocation/generation의 독점 backing을 만들고 모든 slot의 layout을 고정한다.
2. layout_validate 후 format한다. **format은 backing 전체를 지운다.** live mapping이나 이전
   generation에 호출하면 안 된다. 독점 소유·새 generation임은 제공자가 보장해야 한다.
3. Guest와 Worker가 같은 slot을 각각 open한다. 양쪽 open은 제출 전에 끝나야 한다.
   선택한 ring의 head/tail이 0이 아니면 거절한다. 중복 slot/role open 방지도 제공자 책임이다.
4. 제공자가 양쪽 mapping ACK와 binding을 확인한 뒤 Guest 제출을 허용한다.
5. 실패 시 실행 차단·peer 종료·mapping 해제 후 backing을 회수한다. close는 로컬 객체만
   해제한다. peer 통지, pending 취소, unmap, 파일 삭제를 하지 않는다.

초기화 완료 handshake와 실제 RAM mapping 속성 검증은 외부 책임이다. header에는 session
table을 넣지 않는다. 모든 endpoint는 별도의 승인 layout을 받는다. 초기 구현은 hot-add나
동일 generation 재접속을 지원하지 않는다. 변경 시 drain하고 새 generation을 사용한다.

## 호출과 소유권

Guest는 identity와 1부터 증가하는 request ID를 채워 submit한다. 입력은 호출 동안 유효한
private 메모리여야 한다. submit이 복사·게시한 뒤 원본 입력은 재사용할 수 있지만 공유 request
arena는 응답 수신 전까지 덮어쓰지 않는다. mapped pointer를 API의 private buffer로 넘기지 않는다.

Worker take에는 새/빈 request 구조체를 전달하고, 얻은 snapshot은 실행 후 request_release로
해제한다. AGAIN은 실행할 요청이 없는 로컬 상태다. response_capacity로 출력 용량을 실행 전에
확인하고 API/schema 검사·출력 확보 후 실행해야 한다. 미지원 API/schema는 payload 없는
UNSUPPORTED_API 응답으로 처리한다. 이 실행 adapter는 아직 작성하지 않았다.

respond는 pending 요청의 ID/API/schema로 결과를 복사·게시한다. 성공 후 호출자 결과 버퍼를
해제할 수 있다. 이전 응답이 소비되기 전에는 다음 요청을 take하지 않는다. respond의 로컬
인자 오류는 pending을 유지한다. 응답을 수정하거나 abort하되 이미 실행한 CUDA를 반복하지 않는다.

Guest는 private 출력 버퍼를 준비해 receive/try_receive를 호출한다. 용량 부족은 BAD_DESCRIPTOR와
필요한 output_bytes를 반환하고 응답을 소비하지 않는다. deadline 안에 버퍼를 키워 **receive만**
다시 호출한다. payload 복사 후 response head를 반환한다. caller buffer는 library가 free하지 않는다.

함수 반환 0은 전달 성공이다. CUDA 성공은 별도로 response.transport_status/domain/api_result를
확인한다. 함수 반환이 0이 아니면 response의 status 0을 성공으로 해석하지 않는다.

## 제한과 실패

- x86-64 little-endian, 64-byte 정렬 coherent normal RAM, lock-free aligned u64 대상이다.
  코드의 architecture/alignment/lock-free 검사로 실제 VM cache/coherency가 입증되지는 않는다.
- 모든 endpoint API는 같은 owner thread에서 직렬로 호출한다. abort/close도 owner에서 호출한다.
  별도 supervisor thread는 owner에게 이벤트를 전달해야 한다. 외부 동시 호출은 지원하지 않는다.
- ring은 64..65536개 power-of-two slot이지만 occupancy는 최대 1이다. 물리 slot은 순환하고
  u64 counter는 wrap/reset하지 않는다. request ID UINT64_MAX는 게시 전에 거절한다.
- 자신의 counter는 private shadow와 비교하고 peer의 역행·앞지름·occupancy 초과를 거절한다.
  shared descriptor snapshot 이후에만 주소/길이를 사용한다. Guest의 payload 내용 변경 자체나
  동일 VM 안의 악성 프로세스를 하드웨어로 격리하지는 않는다.
- 메시지는 arena 용량과 64 MiB 중 작은 값까지다. backing은 1 MiB..4 GiB다. 64 MiB는 snapshot
  할당 상한이며 10-02 CUDA memcpy의 16 MiB 제한을 확대하지 않는다. chunking/async는 후속 작업이다.
- submit의 QUEUE_FULL은 해당 호출이 미게시임을 뜻한다. 이미 pending인 요청의 재실행 허가는 아니다.
  Worker take의 메모리 부족은 미소비·미실행이므로 take만 다시 시도할 수 있다.
- deadline은 submit에서 시작하는 CLOCK_MONOTONIC 기준 1..60000ms다. timeout은
  EXECUTION_UNKNOWN으로 실패 처리하며 늦은 응답도 거절한다. nonblocking caller도 try_receive를
  호출해야 deadline을 관찰한다. 백그라운드 timer나 자동 재전송은 없다.
- corruption/identity 오류는 최초 원인을 반환한다. 이후 pending 실패 채널은 EXECUTION_UNKNOWN,
  pending 없는 실패 채널은 CLOSED다. 게시된 요청이 실행되지 않았다고 단정하지 않는다.
- idle Worker의 AGAIN에는 timeout이 없다. peer 사망 감지·복구는 supervisor 책임이다.
  abort는 로컬 실패 표시일 뿐 peer에 알리지 않는다. RPC fallback은 없다.

## 검증과 다음 단계

사용자 요청대로 configure·compile·link·정적 검사·테스트·프로세스 간 Queue 실행·Kubernetes
배포·VM mapping·GPU 실행을 전부 수행하지 않았다. 성공한 시험·측정 수치를 기록하지 않는다.
자동 CI, 실행 파일, 배포 entrypoint는 추가하지 않았다. 기존 이미지에는 이 코드가 포함되지 않는다.

후속 검증에는 layout overlap/overflow, ring 순환/counter 손상, duplicate/stale ID,
최대 payload·메모리 부족·작은 응답 버퍼, timeout/늦은 응답/peer 종료, 프로세스 및 실제 VM 간
publication 순서가 필요하다. CPU 프로세스 시험 성공만으로 VM 경계 성공을 간주하지 않는다.

다음 10-04에서 VM 공유 메모리 장치와 mapping adapter, 10-05에서 Controller와 채널 시작/회수
gate, 10-06에서 CUDA payload 및 Guest/Worker 실행 경로를 연결한다.
