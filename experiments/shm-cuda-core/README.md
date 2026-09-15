# 10-06 SHM CUDA core — source authored, NOT_RUN

기본 Runtime malloc/free/device/memcpy/synchronize를 Guest interception → Queue → typed
dispatcher → CUDA backend로 연결했다. Guest는 CUDA SDK header로 빌드하지만 cudart/driver에
링크하지 않는다. Worker만 shared cudart와 HAMi를 사용한다. 일부 Driver memory/device API는
동등한 Runtime 경로로 변환한다. context/launch/library 전체 호환성은 아직 아니다.

schema=1, 기본 scalar는 LE u32/u64다. memcpy payload는 kind u32, reserved u32,
dst handle/offset, src handle/offset, bytes 각각 u64인 48-byte header와 HtoD 입력이다.
DtoH 결과만 raw bytes이고 malloc 결과는 u64 handle이다. 실행 전 응답 용량을 검사한다.
Guest pointer는 PROT_NONE 가상 예약이며 실제 Host/GPU 주소가 아니다. 한 복사는 최대 16MiB다.

Guest는 전용 I/O thread에서 Queue를 사용하고 호출 thread들을 직렬화한다. API last-error는 TLS다.
fork 이후 기존 세션은 거절한다. FLYT_LAYOUT, 명시적 FLYT_IVSHMEM_BDF, FLYT_SLOT이 필요하다.
layout.bin은 channel PVC에서 해당 VM에 별도로 전달해야 한다. 물리 GPU는 Guest에 노출하지 않는다.
초기 HELLO(schema=1, empty payload)로 Guest mapping을 확인하고 이후 API를 허용한다.

Worker 늦은 시작을 위해 10-03 open 규칙을 좁게 확장했다: Worker의 최초 open만 request tail=1,
head=0을 허용한다. consumed/response counter가 있으면 재접속을 거절한다. 첫 요청은 HELLO여야 한다.
wire ABI는 그대로며 이전 10-03 브랜치를 보존했다. Worker별 프로세스는 slot마다 한 번만 생성한다.

Worker runtime_open 안에 HAMi 매핑, 승인 UUID, device count와 메모리 보고값 guard를 연결했다.
이를 interception/quota 성공 검증으로 표시하지 않는다. OOM 등 오류 후 세션 중단 정책은 유지한다.
프로세스 timeout/peer 감지 강화는 10-09, async/compatibility API는 10-07/08에서 추가한다.
모든 compile/link/test/배포/VM/GPU 검증 NOT_RUN. 기존 RPC 경로와 이미지들은 아직 보존돼 있다.
