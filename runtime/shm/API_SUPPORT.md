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

현재 GPU smoke는 text PTX module과 `flytRegisterKernelABI`로 명시한 인자를 사용한다.
`cudaLaunchKernel`은 아직 not-supported이며, PyTorch의 fatbinary/function registration 및
일반 ATen kernel 인자 처리는 구현하지 않았다. 메모리·PTX 시험을 MLP 학습 정확성으로
확대하지 않는다. `scripts/audit-pytorch-shm-abi.py`는 wheel의 정적 import coverage를
보조 자료로 저장하며, 실제 학습 trace나 정확성 검사 결과를 만들지 않는다.
