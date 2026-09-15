# 기존 handler에서 새 실행 경계로의 이관

소스 확인 대상은 인접한 `flyt_custom_for_k8s/cpu`의 runtime/client/resource-map 구현과
이 저장소의 단계별 패치다. 외부 소스 checkout은 수정하지 않았다. 재현용 원본 SHA는
루트 `versions.lock.yaml`의 `596a86939bae125d127a9ed6f70d92c332f47644`이며,
기존 patch series 및 2·3·5·7단계 변경은 보존된다. 새 모듈은 별도 소스다.

| 기존 결합 | 10-02의 분리 | 남은 연결 작업 |
|---|---|---|
| `get_client(rqstp->rq_xprt->xp_fd)` | 명시적 execution session 인자 | 10-05/06의 SHM identity → 세션 선택 |
| `int_result`, `ptr_result`, `mem_result` | typed result + 명시적 release | 10-06 payload encode/decode |
| `cuda_malloc_1_svc`, `cuda_free_1_svc` | backend allocate/release + private allocation table | Guest 가상 device address ↔ handle/offset |
| `cuda_memcpy_htod/dtoh/dtod_1_svc` | 길이·범위 검사 + backend copy | interception 인자와 payload arena 변환 |
| `resource-map`의 주소 기반 조회 | 세션별 단조 증가 ID와 별도 offset | stream/event/module/library typed handle 확장 |
| `cuda_device_synchronize_1_svc`의 client stream 순회 | 세션 전용 프로세스에서 device synchronize | 10-07의 stream/event 순서와 상태 |
| `cpu-client-runtime.c`의 RPC/XDR 제출 | 이번 단계에서는 변경하지 않음 | 10-06 typed payload 생성과 SHM submit |
| `cpu-server-driver*.c`, 라이브러리 handler | 이번 단계에서는 이관하지 않음 | 10-06~08 API별 이관과 지원 목록 |

새 handle은 native GPU pointer도 Guest에서 바로 dereference할 CUDA pointer도 아니다.
10-06 Guest adapter는 별도 가상 주소 공간을 제공하고 pointer arithmetic을 handle/offset으로
변환해야 한다. 이 변환 전에 기존 `ptr_result` 값을 새 handle로 단순 대체하면 안 된다.
kernel 인자에 든 device pointer와 라이브러리 opaque handle도 API별로 변환해야 한다.

10-01의 `flyt_cuda_dispatch` 구현은 다음 순서로 연결한다.

1. 채널/세션 generation, session ID 및 단조 증가 request ID를 검증한다.
2. descriptor와 payload를 Host-private 영역으로 snapshot하고 전체 범위/버전/길이를 검사한다.
3. 지원 API와 payload schema를 확인한 뒤 typed `flyt_cuda_call`로 decode한다.
4. 응답 영역과 필요한 출력 용량을 CUDA 부작용 **전에** 확보한다. Malloc 실행 후 응답 공간이
   없다는 이유로 요청을 다시 실행하지 않는다. timeout 이후도 재실행하지 않는다.
5. `flyt_cuda_exec_call`을 호출하고 status/domain/API 결과와 output을 encode한다.
6. response publication을 완료한 뒤 result 버퍼를 release한다. publication 실패 시 세션을
   실패 처리하고 실행 결과가 불명확함을 표시한다. 새 request ID로 재시도하지 않는다.

SHM request 자체를 이 모듈이 인증하거나 duplicate를 검출하지 않는다. 실행 모듈에 도달하기
전 adapter 책임이다. 한 프로세스에 다른 session ID 요청을 섞어 넣어서는 안 된다.
ring slot 회수와 CUDA async completion은 별개이며, 이번 pageable synchronous copy의 수명
처리를 향후 pinned/async 경로에 그대로 적용하면 안 된다.

공유 Manager, CRD 및 Worker supervisor는 이 단계에서 수정하지 않았다. 실제 세션 프로세스
생성/종료 연결, HAMi guard 재사용, readiness, SHM endpoint 변경, 이미지 통합이 남아 있다.
따라서 기존 RPC 배포가 SHM으로 전환됐거나 모든 handler의 분리가 끝났다고 표시하지 않는다.
