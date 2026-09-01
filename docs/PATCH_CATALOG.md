# Baseline 대비 변경 목록

| Patch | 분류 | 변경 목적 | 대표 검증 |
|---|---|---|---|
| `flyt-k8s-config-paths` | Kubernetes | manager 설정을 `/etc/flyt` 절대 경로에서 읽음 | Pod/VM cwd와 무관한 시작 |
| `flyt-ipc-ftok` | 안정성 | IPC `ftok` 입력의 NUL 종료 보장 | client manager 연결 |
| `flyt-cudnn9` | 빌드 호환 | cuDNN 9에서 제거된 legacy API 조건부 처리 | CUDA 12.8 이미지 빌드 |
| `flyt-pytorch-driver-entry` | Runtime API | 최신 PyTorch driver entry/event/launch 경로 보완 | import, 기본 tensor/kernel |
| `flyt-source-eof` | 재현성 | 소스 EOF 정규화 | patch byte 재현 |
| `flyt-pytorch-host-memory*` | Runtime API | TCP fallback pinned host memory 추적과 복사 | pinned memory round trip |
| `flyt-pytorch-event-map` | Runtime API | event lookup의 성공/실패 판정 수정 | autograd/cuBLAS/AMP |
| `flyt-mig-discovery` | MIG/K8s | NVML 물리 열거 대신 Pod에 노출된 CUDA 장치 탐색 | MIG 1개, 기대 SM 확인 |
| `flyt-mig-cuda-accounting` | MIG | CUDA property 기반 SM·메모리 등록 | Cluster Manager 등록값 |
| `flyt-core-api-set-device` | CUDA semantics | 논리 device 0만 허용 | invalid device 오류 코드 |
| `flyt-core-api-stream-create` | CUDA semantics | stream 생성 시 불필요한 module 재적재 제거 | stream/event 생성 |
| `flyt-mig-memory-quota` | 자원 통제 | 서버 로컬 GPU allocation accounting | 8 GiB quota OOM 경계 |
| `flyt-runtime-function-map` | Runtime API | client 함수 토큰을 서버 `CUfunction`으로 변환 | SDPA 관련 attribute 경로 |
| `flyt-cuda-graph-fail-closed` | 안정성 | 미구현 Graph capture가 원격 handle을 로컬 CUDA에 전달하지 않고 `NOT_SUPPORTED` 반환 | PyTorch Graph가 SIGSEGV 없이 안전 실패 |
| `flyt-client-process-reaper` | lifecycle | 10초 주기 client 감시, 3초 ping timeout, 종료 client 제거 | SIGKILL 후 client 감지 |
| `flyt-cluster-zero-client-deadlock` | lifecycle | zero-client 처리 중 `clients` mutex 재진입 제거 | SIGKILL 후 6초 내 할당 회수 및 CLI 응답 |
| `flyt-public-cuda-abi` | VM CUDA ABI | client export를 공개 CUDA Runtime/Driver 및 검증된 vendor-library 심볼로 제한 | VM에서 `LD_PRELOAD` 없이 표준 SONAME을 통해 Flyt CUDA/cuBLAS/cuSOLVER/cuDNN ABI 로드 |
| `flyt-driver-fallback-fail-closed` | Driver API 안정성 | RPC 미구현 `cu*` fallback과 NULL `cuGetExportTable`을 `CUDA_ERROR_NOT_SUPPORTED`로 바꾸고, `cuGetProcAddress`가 NULL 포인터를 찾지 못한 경우 `CUDA_ERROR_NOT_FOUND`를 반환 | 네이티브 상위 라이브러리 호출이 false success 뒤 NULL 함수·export-table 포인터를 실행하거나 임의 성공 후 SIGSEGV로 진행하지 않고 명시적으로 실패 |
| `flyt-cublas-public-abi` | cuBLAS 공개 ABI | PyTorch 2.11 import에 필요한 누락 심볼을 fail-closed로 제공하고 `cublasLtMatrixLayoutSetAttribute`를 typed RPC로 추가 | `LD_PRELOAD` 없이 versioned cuBLAS/cuBLASLt proxy를 표준 SONAME으로 로드 |
| `flyt-module-load-content` | Driver API/VM 격리 | VM 전용 파일 경로를 GPU Cell에서 다시 열지 않고 `cuModuleLoad` 입력 파일의 내용을 기존 data RPC로 전송 | 서로 다른 파일시스템을 사용하는 VM에서 Triton/JIT 생성 모듈 로드 |
| `flyt-fork-safe-cuobjdump` | Driver API/VM 격리 | cuobjdump helper 실패 시 `_exit`로 inherited RPC cleanup을 차단하고 bundled guest tool 및 정상적인 0-success 반환 규약 사용 | helper child가 부모 RPC를 deinit하는 경쟁 제거 및 VM의 CUDA toolkit 비의존성 확보 |
| `flyt-module-resource-handle` | Driver API resource mapping | `cuModuleGetFunction`이 resource-manager 래퍼가 아니라 저장된 원격 `CUmodule` 주소를 사용 | Triton/JIT module에서 kernel function 조회 |
| `flyt-driver-func-attribute` | Driver API resource mapping | 원격 `CUfunction` token과 attribute enum을 typed RPC로 조회 | Triton/JIT launch 전 function capability 검사 |
| `flyt-driver-pointer-attribute` | Driver API pointer metadata | Triton이 요구하는 `CU_POINTER_ATTRIBUTE_DEVICE_POINTER`를 서버 CUDA에서 검증해 typed RPC로 반환하고 기타 attribute는 fail-closed | Triton/JIT kernel launch argument 검증 |
| `flyt-jit-module-kernel-map` | Driver API JIT metadata | 동일한 이름의 Triton 커널을 모듈별로 별도 보존하고 `CUmodule`과 `CUfunction`을 정확히 연결 | 같은 이름의 커널을 포함한 복수 JIT 모듈의 순차 로드·실행 |
| `flyt-driver-launch-stream-map` | Driver API stream mapping | `cuLaunchKernel`에서 게스트의 원격 stream token 대신 resource manager가 해석한 서버 CUDA stream을 전달 | non-default stream을 사용하는 Triton/JIT kernel launch |
| `flyt-driver-launch-function-map` | Driver API function mapping | function resource wrapper가 아니라 그 안의 실제 서버 `CUfunction` 주소를 launch에 전달 | Triton/JIT kernel의 유효한 function handle 실행 |
| `flyt-vendor-loader-stubs` | Vendor ABI loader completeness | PyTorch shared object가 로드 시 요구하지만 Flyt가 구현하지 않은 vendor algorithm entry를 명시적 `NOT_SUPPORTED`로 제공 | 지원되는 cuSOLVER solve 경로는 로드하고 미구현 batched/X API는 fail-closed |
| `flyt-cusolver-float-solve` | cuSOLVER typed RPC | PyTorch 기본 float32 solve에 필요한 Sgetrf bufferSize/getrf/getrs와 서버 device pointer 매핑 추가 | `torch.linalg.solve` float32 정답·host round trip |
| `flyt-cudnn-loader-stubs` | cuDNN ABI loader completeness | PyTorch가 로드 시 요구하지만 Flyt가 구현하지 않은 RNN/CTC/확장 API를 cuDNN 9 `NOT_SUPPORTED`로 제공 | 기본 convolution RPC 로드 허용, 미구현 알고리즘 fail-closed |
| `flyt-pytorch-build-profile` | 빌드 | PyTorch 2.11, CUDA 12.8, shared cudart, sm_120 | custom wheel 생성 |
| `flyt-cudnn-batchnorm-ex` | cuDNN | legacy BatchNorm Forward/Backward Ex typed RPC | PyTorch cuDNN legacy 경로의 학습형 BatchNorm 지원 |
| `flyt-cudnn-batchnorm-workspace` | cuDNN | BatchNorm Ex workspace/reserve 크기 질의 RPC | Ex 실행 전 필수 버퍼 계약 지원 |
| `flyt-cudnn-convolution-backward` | cuDNN | convolution backward data/filter/bias typed RPC | PyTorch CNN 학습 역전파 지원 |
| `flyt-runtime-function-attributes` | CUDA Runtime | 실제 kernel binary/PTX version 및 함수 속성 RPC | SDPA의 binary/device architecture 검증 지원 |
| `flyt-cufft-xt-rpc` | cuFFT | plan/stream/work-area/Xt plan·execute typed RPC | PyTorch `torch.fft`의 원격 GPU 포인터 지원 |
| `flyt-cusparse-spmm-rpc` | cuSPARSE | COO/CSR·dense descriptor 및 SpMM typed RPC | PyTorch sparse-dense 행렬곱 지원 |
| `flyt-cusparse-loader-stubs` | cuSPARSE | 미지원 알고리즘 85개 fail-closed loader 심볼 | libtorch 로딩 완결성과 명시적 미지원 반환 |
| `flyt-runtime-mempool-async` | CUDA Runtime | default mempool과 `cudaMallocAsync`/`cudaFreeAsync` typed RPC 및 원격 pointer 수명 관리 | PyTorch async allocator |
| `flyt-runtime-cuda-graph-rpc` | CUDA Graph | stream capture와 graph instantiate/launch/destroy typed RPC, transport thread에 독립적인 RELAXED server capture | Graph capture/replay |
| `flyt-runtime-capture-info-rpc` | CUDA Graph | capture status와 graph ID 조회 직렬화 | PyTorch capture 상태 검사 |
| `flyt-cublaslt-algo-rpc` | cuBLASLt | heuristic algorithm 결과의 고정 크기 직렬화 | matmul warm-up 및 Graph capture |
| `flyt-runtime-graph-get-nodes-rpc` | CUDA Graph | node 개수와 원격 node handle 배열의 길이 검증 직렬화 | PyTorch graph topology 조회 |
| `flyt-client-capture-mode-tls` | CUDA semantics | guest thread-local capture mode를 client TLS에 유지 | VM 애플리케이션의 thread-local CUDA 동작 |
| `flyt-driver-graph-runtime-bridge` | Driver/Runtime ABI | `cuStreamEndCapture` Driver entry를 typed Runtime Graph RPC에 연결 | PyTorch Driver ABI capture 종료 |

패치의 원문과 적용 순서는 각각 `patches/*.patch`, `patches/series`가 유일한
기준이다. 배포 YAML에 patch 내용을 복제하지 않고 ConfigMap을 실행 시 생성한다.
