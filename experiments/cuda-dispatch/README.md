# 10-02: 전송 계층과 분리된 CUDA 실행 모듈

**기본 Runtime 실행 모듈 소스 작성 완료, 검증 NOT_RUN, runtime 미연결.**
`stage/10-01-shm-contract`의 `1a35dce6e01539a4078429cb951f9ea517598301`에서
`stage/10-02-cuda-dispatch`를 분기했다. 이전 단계와 RPC 배포 경로는 보존한다.

이 단계는 기존 `cpu-server-runtime.c`에서 RPC client 조회, CUDA 실행, XDR 결과 수명이
섞여 있던 책임을 새 SHM 경로용 모듈로 분리한다. 기존 모든 CUDA handler를 옮긴 것은 아니다.
기본 Runtime API의 실행 본체와 확장 경계를 작성했으며, 기존 RPC handler가 이 모듈을
호출하도록 패치하거나 RPC와 SHM을 동시에 운영하는 adapter는 추가하지 않았다.
Guest interception과 payload 변환은 10-06, async/Graph/라이브러리는 10-07~08 범위다.

## 코드와 경계

| 파일 | 책임 |
|---|---|
| `include/flyt_cuda_exec.h` | Host-private typed call/result, backend 함수 표, 세션 API |
| `src/exec.c` | API dispatch, 세션별 allocation handle, 범위 검사, 결과 버퍼 소유권, 종료 |
| `src/runtime.c` | RPC 호출 없이 CUDA Runtime API를 직접 호출하는 backend |
| `CMakeLists.txt` | CPU core와 선택적 CUDA backend의 별도 static library 구성 |
| [MIGRATION.md](MIGRATION.md) | 기존 구현 대응과 후속 adapter의 필수 처리 |
| `versions.json` | 부모 커밋, 구현 범위, 미실행 검증 기록 |

`svc_req`, RPC socket fd, XDR union, rpcgen stub은 실행 모듈의 입력이 아니다.
10-01의 API ID·오류 상수는 재사용하지만 native C struct는 wire ABI가 아니다.
`flyt_cuda_exec_call`은 이미 decode된 Host-private 호출을 받는다.
10-01의 `flyt_cuda_dispatch`, `flyt_shm_submit`, `flyt_shm_receive`는 아직 선언만 존재한다.
SHM payload decoder를 임의로 완성된 것으로 처리하지 않는다.

## 구현된 실행 범위

| API | 새 실행 처리 |
|---|---|
| GetDeviceCount / GetDevice | 실제 CUDA 조회, 노출 device가 1개이고 ordinal 0이라는 전제 |
| SetDevice | ordinal 0만 허용, 다른 값은 CUDA invalid-device 오류 |
| Malloc / Free | 실제 CUDA 호출, Host 주소 대신 세션별 단조 증가 handle 반환 |
| Memcpy | HtoD / DtoH / DtoD, handle + offset + bytes 검사 |
| DeviceSynchronize | 세션 전용 프로세스의 CUDA 동기화 |
| 나머지 API ID | backend 호출 없이 `FLYT_SHM_UNSUPPORTED_API` |

Driver API, kernel/module/function, stream/event, Graph, pinned/managed/async allocation,
cuBLAS/cuDNN 등은 아직 지원하지 않는다. PyTorch를 실행할 수 있다는 의미가 아니다.
기존 MPS scheduler, checkpoint, recorder, IB 및 RPC 특화 복사 경로는 가져오지 않았다.
HAMi 메모리 quota를 별도 allocation 합계로 다시 제한하지 않으며 실제 제한은 HAMi가 담당한다.

## 수명과 오류

- 실행 세션은 프로세스 수명 전체에 한 번만 생성한다. 종료 후 다른 client에 재사용하지 않는다.
  한 VMI 안의 여러 client는 후속 supervisor가 서로 다른 실행 프로세스로 분리해야 한다.
- 생성·호출·정리는 동일 전용 thread에서 수행한다. 외부 동시 호출은 지원하지 않는다.
  다른 thread의 실행 요청은 거절한다. CUDA 초기화 후 fork하여 세션을 만들지 않는다.
- handle 0은 null이다. 살아 있는 allocation은 최대 4096개이며, 해제한 slot은 재사용해도
  handle 번호는 재사용하지 않는다. handle overflow 전에 거절한다.
- 범위는 `offset <= allocation_bytes`, `bytes <= allocation_bytes - offset`으로 검사한다.
  Host 주소를 Guest 값으로 cast하지 않는다. DtoD의 동일 allocation 내 겹치는 복사는 거절한다.
- 한 copy는 최대 16 MiB다. 초과 요청과 알 수 없는 handle은 transport 오류다.
  0-byte copy도 유효한 device handle을 요구한다. 이 초기 제한을 CUDA 전체 호환성으로 보지 않는다.
- HtoD는 Host-private snapshot을 사용하고 DtoH 결과는 모듈 소유 버퍼에 받는다.
  adapter는 결과를 response 영역에 복사한 후 `flyt_cuda_result_release`를 호출한다.
  결과 구조체 재사용 전에도 release가 필요하다. 공유 영역 포인터를 직접 넘기면 안 된다.
- transport 성공과 CUDA 성공은 다르다. `status == FLYT_SHM_OK`일 때만 Runtime domain의
  `api_result`를 해석한다. CUDA 오류에는 결과 payload/handle을 반환하지 않는다.
- allocation/copy/free/sync의 CUDA 오류는 원래 오류를 반환하고 세션을 종료 상태로 바꾼다.
  보수적으로 OOM도 세션 종료 대상이다. 이후 호출은 CLOSED, destroy는 원래 오류와
  INTERNAL_ERROR를 반환하며 CUDA를 재호출하지 않는다. 불확실한 free를 재시도하지 않는다.
  supervisor가 해당 세션 프로세스를 종료해야 한다. 이 supervisor 연결은 후속 단계다.
- 정상 종료는 새 호출 차단 → synchronize → tracked allocation free 순서다.
  cleanup 중 오류가 나도 동일하게 process 종료가 필요하다. GPU reset은 호출하지 않는다.

## Worker에 연결하기 전 필수 조건

CUDA backend open은 현재 Worker의 HAMi preload, 승인 GPU UUID, quota, VMI/Worker/Pod UID 및
generation 확인 **이후** 호출해야 한다. open 자체는 device count/ordinal만 확인하며 기존
5단계 guard를 대체하지 않는다. 이번에는 standalone 실행 파일이나 새 배포 entrypoint를 만들지
않았다. guard 없이 새 backend를 실행해도 안전한 배포 경로가 완성됐다는 주장을 하지 않는다.

향후 Worker는 새 실행 프로세스에서 guard → runtime_open → exec_create 순서로 초기화한다.
세션 identity 검증·요청 순서·payload snapshot → typed call → result 복사/release 순으로
처리하고, 정상 종료에는 exec_destroy 성공 후 runtime_close 및 process exit를 수행한다.
오류 시 해당 프로세스만 종료하고 이전 generation을 재사용하지 않는다.

## 빌드 구성과 검증 상태

CMake는 기본적으로 CUDA SDK 없는 CPU core만 대상으로 하고, 명시적으로
`FLYT_BUILD_CUDA_RUNTIME=ON`을 지정하면 CUDA 12.8 이상 Toolkit의 shared cudart에 연결하도록
작성했다. static library 형태이며 자동 실행, 이미지 제작, 배포, CI는 추가하지 않았다.
기존 이미지가 새 코드를 자동으로 포함하지 않는다. shared cudart 사용만으로 HAMi interception이
검증되는 것은 아니며, 후속 이미지 통합 시 preload와 실제 진입 경로 검증이 필요하다.

사용자 요청에 따라 CMake configure, 컴파일, 링크, 정적 검사, 테스트, 패치 적용 검사,
Kubernetes 배포, GPU 실행을 수행하지 않았다. 8·9단계 및 CPU 배포 검증도 계속 보류한다.
향후에는 별도 프로세스별 fake-backend 시험, 오류 뒤 재호출 차단, stale/bounds/overlap,
buffer 수명, CUDA 결과, HAMi interception, 실제 SHM 경로를 검증해야 한다.
이번 단계에 성공을 반환하는 mock 구현은 없다.

다음 단계는 10-03의 Queue·payload·encode/decode·polling 구현이다. Queue 이후에도 VM mapping,
Controller, Guest API adapter가 남으며 RPC 전체 제거는 10-10에서 수행한다.
