# 현재 SHM 실행 범위와 메모리 조회 확장

실제 검증과 미실행 항목은
[2026-09-22 보고서](../../experiments/evidence/IMPLEMENTATION_AND_VALIDATION_2026-09-22.md)를 따른다.
함수 export 여부만으로 PyTorch 호환성을 판정하지 않는다.

## 메모리 조회

`FLYT_API_RUNTIME_MEM_GET_INFO = 0x1013`을 추가했다. 기존 opcode와 layout을 바꾸지 않은
추가 연산이며 CRD 변경은 없다. payload schema는 1, 요청 payload는 0 byte다.
응답은 `free_bytes`와 `total_bytes`의 64-bit little-endian 정수 두 개, 총 16 byte다.

Worker는 CUDA Runtime을 통해 HAMi 적용 후 실제 값을 조회한다. 응답 공간과 session을
검증한 뒤 호출하며, 오류가 나면 CUDA Runtime 오류와 빈 payload를 반환한다. total=0 또는
free>total인 모순된 성공 응답은 unknown 오류로 처리한다. 요청 quota를 조회값처럼 만들지 않는다.

Guest의 `cudaMemGetInfo` 및 `cuMemGetInfo_v2`는 이 경로를 사용한다. 이전 Worker는
새 opcode를 unsupported로 거절할 수 있으므로 Guest/Worker digest를 함께 고정한다.
`tests/integration/memory_info_dispatch.c`는 인코딩, 호출 전 응답 크기 검사,
CUDA 오류 전달과 모순된 반환값 거절을 검사한다. 실제 VM의 1 GiB·4 GiB 조회·경계 검사는
보고서에 별도 기록한다.

## OOM과 kernel 실행

cudaMalloc의 일반 메모리 부족은 session의 치명적 오류가 아니다. 기존 allocation을 해제하고
작은 요청을 재시도할 수 있어야 한다. 나머지 불확실한 CUDA 오류의 보수적 종료는 유지한다.
`allocation_oom_recovery.c`와 실제 VM의 OOM 후 재할당으로 이 구분을 검증했다.

기존 GPU smoke는 text PTX module과 `flytRegisterKernelABI`로 명시한 인자를 사용한다.
이 경로와 기존 opcode는 유지한다. 새 학습 경로는 아래의 명시적 opt-in을 사용한다.
`scripts/audit-pytorch-shm-abi.py`는 정적 import coverage이며 실제 실행의 증거가 아니다.

## FP32 eager 학습 경로

지원 대상은 고정된 `torch-2.11.0+flyt.cu128-cp310-cp310-linux_x86_64.whl`,
CUDA 12.8, x86-64 little-endian Guest/Worker, 단일 CUDA context/장치다.
`FLYT_MIRROR_DEVICE_VA=1`과 Guest `libflyt_guest.so` preload가 필요하다.
PyTorch의 명시적 `dlopen("libcuda.so.1")`도 Guest 중계 라이브러리에 연결하도록 같은
디렉터리에 `libcuda.so.1 -> libflyt_guest.so` 링크를 둔다.

- Runtime fatbinary/function 등록을 Guest에 저장하고 첫 커널 호출 때 해당 fatbinary를
  64 KiB chunk로 Worker에 보낸다. 하나의 미완료 업로드와 총 미완료 업로드는 512 MiB로 제한한다.
- Worker가 `cuFuncGetParamInfo`로 실제 인자 크기를 조사한다. Guest가 보낸 인자 수·크기를
  Worker가 다시 확인한다. 최대 64개 인자, 크기 표기를 포함한 32,764-byte payload 한도다.
- 구조체 내부 포인터를 추측하거나 64-bit 정수 값을 포인터로 간주하지 않는다. opt-in
  `cudaMalloc`은 Worker GPU 가상주소와 같은 주소를 Guest에 `PROT_NONE`으로 예약한다.
  예약 충돌·비정렬 주소·주소 범위 초과는 실패하며 기존 CPU 매핑을 덮어쓰지 않는다.
  GPU 실행 인자의 바이트를 보존하므로 내부 offset 및 구조체 안의 GPU 포인터도 유지된다.
- 이 모드의 CUDA 할당 요청은 4 KiB로 올림한다. HAMi에는 올림한 실제 할당이 계상된다.
  일반 복사·free·cuBLAS API는 계속 allocation ID와 offset을 검증한다.
  인접 allocation의 시작 주소를 이전 allocation의 끝 주소보다 먼저 찾는다.
- 장치 속성, 버전, primary context 상태, stream 우선순위와 memset을 Worker에서 조회·실행한다.
  장치 속성은 필드별 little-endian 형식이며 native 구조체 padding을 전송하지 않는다.
- cuBLAS SGEMM, stream/workspace 설정, math mode와 host pointer mode를 지원한다.
  공통 학습 프로그램은 `DISABLE_ADDMM_CUDA_LT=1`, SGD `foreach=False`, `fused=False`를
  모든 비교군에 동일하게 적용한다. 이 결과는 cuBLASLt 기본 경로의 검증이 아니다.
- DtoH async 복사는 결과를 반환하기 전에 stream 완료를 기다리는 보수적 구현이다.
  `FLYT_TRACE_CALLS`는 개발 진단용이며 성능 실행에서는 제거한다.

새 schema-1 opcode: 0x2040..0x2044는 바이너리 업로드·커널 layout·packed launch,
0x2100..0x2107은 장치 속성·attribute·버전·device VA·memset·stream 우선순위·context 조회다.
device VA 응답은 GPU 주소의 명시적 정수 표현이며 Worker의 CPU 버퍼 주소가 아니다.
이 기능은 임의 Guest CPU 포인터, managed/registered host memory, multi-device context,
PTDS, CUDA Graph capture, NCCL/DDP 또는 PyTorch 전체 API 지원을 의미하지 않는다.
커널의 메모리 접근 자체에 대한 별도의 보안 격리를 입증하는 실험도 아니다.

등록된 static device 데이터는 Worker가 읽는 fatbinary에 포함된다. Guest의
`cudaGetSymbolAddress`는 명시적으로 미지원이다. 지원하지 않는 함수 주소를 native
NVIDIA Driver 라이브러리에서 대신 찾아 반환하지 않는다.

실제 학습 판정과 알려진 제약은
[PyTorch 구현·검증 보고서](../../experiments/evidence/PYTORCH_IMPLEMENTATION_2026-09-22.md)를 따른다.
