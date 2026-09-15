# 10-08 compatibility — 부분 지원 소스 작성, NOT_RUN

SHM 전용 Graph handle/빈 노드 dependency/instantiate/launch/destroy, cuBLAS handle/stream/SGEMM,
cuDNN handle 생성·해제/version 경로와 Driver proc-address allowlist를 작성했다.
미지원 lookup은 native CUDA 주소를 돌려주지 않는다. 표의 authored는 실행 검증을 뜻하지 않는다.

| 기능 | 현재 범위 |
|---|---|
| 기본 Runtime/Driver memory | 10-06 작성, 16MiB 복사 상한 |
| stream/event, async | 10-07 작성, DtoH는 완료까지 blocking |
| PTX Driver kernel | 명시적 flytRegisterKernelABI 필요 |
| Graph | 빈 node DAG lifecycle; compute/memcpy node 및 capture 미지원 |
| cuBLAS | host alpha/beta, column-major float SGEMM, 양수 차원, matrix bounds 검사 |
| cuDNN | handle/version만; tensor/convolution/activation 연산 미지원 |
| PyTorch | unmodified PyTorch 호환 미완료; fatbinary/Runtime registration 등 미지원 |
| 기타 라이브러리 | cuBLASLt/cuSOLVER/cuSPARSE/cuFFT 미지원 |

Graph/library 전체 이관을 완료했다고 표시하지 않는다. 특히 기존 MPS의 PyTorch 19/19 결과를
이 SHM 경로에 승계하지 않는다. 전체 PyTorch 호환 개발에는 해당 API/registration/loader의 추가
이관이 필요하다. 이후 10-09/10은 이 명시적 지원 집합을 기준으로 수명주기와 RPC 제거를 진행한다.

Runtime stream capture/managed allocation/device reset 및 Runtime cudaLaunchKernel는 명시적으로
거절한다. Guest에는 물리 GPU를 제공하지 않아야 한다. 시스템의 native CUDA library로 우회하는
애플리케이션/직접 dlopen 동작까지 이 preload library가 포괄적으로 차단하는 것은 아니다.
10-10 배포는 SHM 전용 artifact만 제공하며 필요한 미지원 심볼을 성공 stub으로 채우지 않는다.

정적 검사·컴파일·링크·Graph/cuBLAS/cuDNN/PyTorch 실행은 모두 NOT_RUN이다.
