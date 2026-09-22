# PyTorch SHM 학습 경로 구현·검증 — 2026-09-22 KST

대표 FP32 eager MLP·SGD를 실제 **Guest → ivshmem/SHM → Worker → HAMi → 지정 GPU**
경로에서 실행했다. 서로 다른 seed의 두 VM에서 batch 32/1024, GPU 상주/매 step 전송의
8개 조건이 100 step을 완료했다. 같은 물리 GPU를 직접 할당한 passthrough VM과
1·100 step의 loss·모든 gradient·갱신 parameter를 비교해 모두 통과했다.
관측한 최대 절대오차는 0이며, tolerance 초과·NaN/Inf·gradient 누락은 없었다.
시작 전 취소 회수 수정 후 새 allocation의 단일 VM 네 조건도 다시 실행했고,
passthrough 및 동시 실행 결과와 모두 정확히 일치했다. 정상 회수·재사용까지 확인했다.

이는 **D3 대표 학습 경로의 개발 검증**이다. RPC·MPS, CPU/NUMA 배치와 연산 제한 명세,
독립 성능 반복이 남아 있으므로 원래 계획의 E1~E5 전체 또는 논문 성능 실험 완료를
뜻하지 않는다. 모든 실행은 `formal_result=false`로 보존한다.

## 주장과 증거

| 주장 | 실험 ID | 판정 기준 | 실제 결과 | 한계 |
|---|---|---|---|---|
| 실제 compiled CUDA kernel의 인자 전달 | ABI-VM-v3 | 구조체 안 GPU pointer·offset·주소처럼 보이는 scalar 바이트 보존, GPU 결과 일치 | PASS, `[2,5,7]` | 고정 64-byte 구조체 probe |
| 단일 SHM VM의 학습 | SHM-v4, seed 2026 네 조건 | 100 step 완료, 1/100 step 전수 비교 | host native 진단과 PASS, 최대 절대오차 0 | 초기 다중 session 개발 allocation이므로 Channel Ready 증거로 사용하지 않음 |
| 두 SHM VM의 독립 학습 | SHM-final2-a/b | Ready 상태의 실제 두 VM, 다른 fixture, 각 네 조건 성공 | 8/8 PASS, 두 Worker GPU process 동시 관측 | 기능 검증이며 중앙 60초 처리량 시험 아님 |
| 수정 후 단일 VM 재검증 | SHM-final-single2 | 같은 GPU/학습 binary, compute 100, 네 조건과 정상 회수 | 4/4 PASS; passthrough·pair 모두 최대 절대오차 0; Released 및 Worker 삭제 | controller v2 결과는 v1 pair와 별도 버전으로 보존 |
| passthrough 대비 학습 정확성 | PT-PAIR-8 | `abs(got-ref) <= 1e-6 + 1e-4*abs(ref)` | 8/8 PASS, 조건별 18개 tensor 비교, 최대 절대오차 0 | 고정 모델·SGD·FP32·eager 범위 |
| 기준 자체의 반복 일치 | PT-REPEAT-4 | seed 2026 네 조건을 새 프로세스로 반복해 같은 검사 | 4/4 PASS, 최대 절대오차 0 | 예비 안정성 검사, 성능 5회 반복 아님 |
| 학습 후 정상 회수 | SHM-final2-a/b | tensor 회수, guest exit 0, Released, Worker Pod 삭제 | 양쪽 PASS | 이전 버전의 생명주기 20회 결과와 합산하지 않음 |
| 비 GPU 회귀 | REGRESSION | 잘못된 wire/주소/메모리 조회 거절, OOM 회복, 기존 제어 동작 | C 검사 5개, layout 6개, controller 30개, evidence 17개 PASS | mock/CPU 검사는 GPU 실행 증거가 아님 |

공개 증거는 [results/2026-09-22-pytorch](results/2026-09-22-pytorch/)에 있다.
`comparisons/`에는 tensor별 오차와 reference/candidate SHA256을, 각 실행 디렉터리에는
manifest·metrics·tensor hash를 보관한다. `shm-final2-a/b/route.json`은 실제 Channel,
Worker image·HAMi allocation·VMI를 연결한다. `passthrough-route.json`과
`evidence/passthrough-environment.txt`는 실제 VFIO 경로와 guest native driver·UUID를 연결한다.
`shm-final-single2/`는 제어기 수정 후 정상 학습·회수 재검증이다.
실행된 제어 버전은 pair와 후속 취소 수정 단계로 구분하며, 서로 다른 버전의 반복 횟수를 합산하지 않는다.
원본 tensor·실패 로그·전체 배포 snapshot은 `.local/pytorch-path-20260922`에 보존했다.

## 고정한 환경과 실행 조건

- 노드 gpu-4, 물리 GPU `GPU-7d708c42-8d4a-16d5-0746-474567157aa3`,
  RTX PRO 6000 Blackwell Server Edition, BDF `0000:41:00.0`, IOMMU 그룹 26 단독 장치.
- SHM과 passthrough는 동일 GPU를 시간적으로 나눠 사용했다. native host 진단 결과에는
  `native-diagnostic` 이름을 사용하며 passthrough라고 표기하지 않았다.
- 두 방식의 guest는 Ubuntu 22.04.5, kernel `5.15.0-191-generic`, Python 3.10.12,
  8 vCPU·16 GiB RAM, 같은 원본 Ubuntu disk와 16 GiB root 용량을 사용했다.
- CUDA 12.8, NVIDIA driver 580.173.02, HAMi 2.10.0, KubeVirt 1.9,
  `torch-2.11.0+flyt.cu128-cp310-cp310-linux_x86_64.whl`을 사용했다.
- CPU 생성 fixture seed 2026/2027을 동일 파일로 복사했다. 모델은
  `Linear(1024,2048) → ReLU → Linear(2048,1024)`, MSE, SGD lr 0.001,
  momentum/weight decay 없음, FP32, TF32 비활성화다.
- 모든 방식에서 `DISABLE_ADDMM_CUDA_LT=1`, `foreach=False`, `fused=False`를 고정했다.
  cuBLASLt 기본 경로까지 지원한다고 주장하지 않는다.
- 두 VM에는 각각 4096 MiB, compute 50, single session을 제공했다. Worker Pod의
  실제 GPU resource와 HAMi annotation을 보존했다. 이 설정 전달은 compute limiter
  정확성 입증이 아니며, Worker host CPU pinning/4-core budget 검증도 아직 아니다.
- 두 SHM VM은 같은 프로세스/세션 안에서 네 조건을 순차 실행했다. 각 조건의 model과
  optimizer는 fixture로 재초기화했다. passthrough 각 조건 및 반복은 새 프로세스다.
  독립 프로세스 5회의 성능 방법과 혼합하지 않는다.

| 구성요소 | 실제 실행 image digest 또는 해시 |
|---|---|
| Worker | `sha256:a7bae7a184238df59b16cce0233ed6dbc74a135c3b3295b0c97811c2c9e9e47d` |
| Control plane (pair 실행) | `sha256:a60f76638461c0fb15c11d89779c482e44621ab52119e682b279de0dd2a4859d` |
| Control plane (시작 전 취소 수정) | `sha256:8dd0a2f445244a839ce390e83da5ad653cd82c6904dced06fc777dc570ccb808` |
| Hook | `sha256:ff8db37b7ee864c5b63335986b2d4c85c550ca7bd82c80ed241563f715fa0554` |
| ivshmem launcher | `sha256:3a63c98b5a178af7f58f8979e09cbd11182f7220e5826f5048efb22a2d7b7935` |
| 16 GiB guest image | `sha256:47e520785c49e6db93631ef4876dcb40b73b23ffc766327e6b02cdfe3496c052` |
| Guest shim SHA256 | `a82a3a818b335cbca7ee9d986ae8b1dc7d14915b09f6bdf9351fde1bfd48c401` |
| train.py SHA256 | `3fb012a38bfcb1a2840d3eb71347e2cc9410845e88a735ab3c7cd71405c2af56` |

실행 당시 base commit은 `f1e0f86`이며 미커밋 구현을 빌드했다. 실제 실행 파일·소스 해시를
manifest와 `source-sha256.json`에 연결한다. 이미지 바이너리는 노드 로컬 OCI이며
GitHub에 registry image나 수백 MiB tensor를 업로드하지 않는다. OS package 저장소를
snapshot으로 고정하지 않았으므로 재빌드 digest 동일성을 보장하지 않는다.

## 구현 내용

1. **Runtime 등록과 커널 실행**: `__cudaRegisterFatBinary/Function`을 Guest에 저장하고
   첫 사용 시 fatbinary를 64 KiB 단위로 Worker에 전달한다. Worker가 실제 CUDA
   `cuFuncGetParamInfo`로 인자 크기를 조사하고 packed launch의 수·길이를 다시 검사한다.
   기존 PTX/명시적 ABI opcode는 유지했다.
   native trace의 네 조건에서 kernel 변형 9종, 최대 1048-byte parameter가 관측돼
   기존 16-byte 인자 제한을 넘어서는 전달이 필요함을 확인했다. trace의 enter/exit는 모두 짝이 맞았다.
2. **GPU 가상주소 보존**: `FLYT_MIRROR_DEVICE_VA=1`에서 native CUDA allocation 주소와
   같은 Guest VA를 `PROT_NONE|MAP_FIXED_NOREPLACE`로 예약한다. ATen 구조체의 내부
   pointer/offset을 바꾸지 않고 전달한다. 주소처럼 보이는 정수의 heuristic 변환은 하지 않는다.
   충돌·비정렬·overflow는 실패하며 기존 CPU mapping을 덮어쓰지 않는다.
   CUDA allocation은 4 KiB로 올림하며 실제 올림된 요청이 HAMi에 전달된다.
3. **필수 API**: 장치 속성·attribute·버전·primary context 상태, stream priority,
   memset, cuBLAS math/pointer mode/workspace를 추가했다. 속성은 native 구조체가 아닌
   필드별 little-endian wire 형식으로 전달한다. PyTorch가 명시적으로 여는
   `libcuda.so.1`도 Guest shim에 연결해 native Driver fallback을 막았다.
4. **실행/판정 도구**: 같은 Python/CUDA bundle 배포, 실제 BAR 탐색, single-session suite,
   Channel Ready·결과 회수·정상 drain을 수행하는 `run-evidence-training.py`를 추가했다.
   비교기는 프로그램/torch/CUDA/SGD/BLAS 설정 불일치도 거절한다.
5. **진단/회귀**: CUPTI Runtime/Driver trace와 실제 kernel parameter layout을 기록하는
   도구, 구조체 ABI GPU probe, 인접 주소·VA 충돌·잘못된 wire regression을 추가했다.

6. **시작 전 취소 회수**: 이미지 준비 중 삭제된 launcher는 Pod가 Failed여도 main
   container 상태가 PodInitializing으로 남을 수 있었다. 기존의 모든 container terminated
   조건을 유지하면서, 한 번도 Mapped되지 않은 attachment에 한해 별도 근거를 허용했다.
   삭제 요청·Failed·Initialized=False·sandbox 종료, 모든 명시적 container status,
   main의 ID/실행/재시작 이력 부재, 실행된 init의 종료, Ready 노드를 모두 요구한다.
   정보 누락·이전 mapping·살아 있는 init·Node NotReady를 거절하는 회귀 검사를 추가했다.
   실제 보류 건은 수정 controller에서 Detached 증거 저장 후 Released·Pod 삭제됐다.
   `cancel-recovery.json`에 한 건의 실제 회복을 보존한다. 이 근거는
   [Kubernetes Pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)과
   실제 보존된 kubelet status를 함께 검토한 제한된 처리이며 Pod 부재만으로 통과시키지 않는다.

공개 CRD와 기존 SHM layout 변경은 없다. 새로운 추가 opcode와 동일 버전 Guest/Worker
조합이 필요하다. 최대 64 kernel parameters, 32,764-byte packed payload,
총 512 MiB의 미완료 module upload 제한을 둔다.

## 발생한 문제와 조치

| 문제/관측 | 원인 또는 확인 범위 | 조치와 재검증 |
|---|---|---|
| CUPTI trace 중 SIGSEGV | 설치 조합에서 callback의 optional `symbolName` 접근 중 fault | 해당 필드 역참조 제거, Runtime 등록 이름으로 추적. 학습 및 네 조건 trace 재실행 성공 |
| 계측 후 PyTorch launch error | parameter 종료 탐색이 남긴 CUDA last-error | metadata 탐색 전 상태를 확인하고 탐색 오류만 정리. native 재실행 성공 |
| 2 GiB Guest root 부족, 부분 복사 | Python/CUDA bundle 및 layout 복사가 완결되지 않음 | 초기 진단 tmpfs 복구 후 최종 16 GiB image와 5 GiB 사전 여유 검사 적용 |
| 첫 ABI VM probe 실패 | 일부 복사 파일/layout이 불완전 | layout·라이브러리 재배포, 실제 GPU probe-v3 성공 |
| PyTorch `libcuda.so.1` 없음 | PyTorch의 명시적 dlopen이 preload 이름과 다름 | shim alias 제공, native GPU 없이 SHM 초기화 성공 |
| `cuCtxGetCurrent` 등 누락 | 실제 eager/autograd가 요구하는 Driver entry | 지원 primary-context 경로 추가, unsupported entry는 native fallback 없이 거절 |
| backward 중 잘못된 allocation 참조 | 인접 allocation 시작 주소가 앞 allocation의 one-past로 먼저 매칭 | 내부/시작 주소 우선 조회, 경계 회귀 검사 및 100 step 재실행 성공 |
| 초기 multi-session 개발 VM이 Ready에 도달하지 않음 | 여러 slot의 순차 사용은 모든 session mapped를 요구하는 Ready 계약과 다름 | 최종 시험은 VM당 single session, 실제 Ready 증거 수집. 초기 결과를 Ready 통과로 승격하지 않음 |
| 첫 최종 pair runner 실패 | helper 인자 `text`가 subprocess `text=True`와 충돌 | 인자 이름 수정. 최초 pair는 FAIL로 보존, 새 allocation final2-a/b 모두 PASS |
| native VM 생성 admission 거절 | SHM 전용 namespace의 요청/채널 정책 | 별도 baseline namespace에서 생성. SHM 정책 완화 없음 |
| `hami.io/device-cordon`만으로 GPU 제외 안 됨 | 해당 annotation 상태에서도 대상 UUID 할당 관측 | nodeconfig `filterdevices.uuid` 적용·plugin 재시작. 실제 probe가 UUID mismatch로 Pending임을 확인 |
| NVIDIA unbind 지연 | compute 작업은 없지만 HAMi/NVML/DCGM이 장치 FD 보유 | 대상 노드 모니터링 Pod 재시작 후 VFIO bind 성공. 원복 뒤 다시 등록/Ready 확인 |
| passthrough 재부팅 후 SSH 불가 | cloud-init instance-id가 firmware UUID여서 이전 NIC 설정 재사용 | native VM에 새 UUID·고정 MAC·명시적 networkData 적용. 동일 root disk에서 GPU 인식·학습 성공 |
| 시작 전 취소 후 Draining 유지 | 실행되지 않은 container의 terminated 상태를 요구해 증거 판정 불가 | sandbox 종료·미실행 상태를 확인하는 좁은 규칙 추가. controller 30개 검사 및 실제 보류 건 회복 PASS |
| 최종 단일 재실행 ImagePullBackOff | 로컬 image가 사라짐. 노드에 image GC 및 registry localhost 연결 실패 관측 | 보관 OCI에서 같은 digest 재import, 실패/지연 기록 보존. 장기 배포는 pull 가능한 registry가 필요 |

실패 이력의 원시 로그는 삭제하지 않았다. 첫 pair와 이미지 부족으로 시작 timeout된 단일 시도의 FAIL/회수 결과는 공개
`failures/`에도 보존한다. 수정 전 실패를 최종 PASS로 덮어쓰거나 개발 버전 성능을 합산하지 않았다.

## 원복과 미완료 범위

passthrough 실행 후 VMI/launcher 종료를 확인하고 GPU를 NVIDIA driver로 되돌렸다.
`driver_override`를 비우고 persistence Disabled를 복원했다. HAMi ConfigMap의 임시 GPU
filter와 node cordon, KubeVirt의 임시 permitted device 항목을 제거했다. 4개 물리 UUID가
다시 HAMi에 등록되고 모니터링 Pod가 Ready임을 확인했다. 임시 privileged 관리 Pod는 삭제했다.
기존 `flyt-infra-validation/basic-vm`과 이전 회수 보류 allocation은 변경하지 않았다.
기준 VM은 Halted로 보존하고 root PVC는 Retain 상태로 유지한다.
최종 감사 `final-audit.json`에서 대상 GPU compute process 없음·4개 GPU 사용 메모리 0 MiB,
이번 PyTorch Channel 7개 모두 Released, 실험 VMI·Worker·관리 Pod 부재를 확인했다.

| 항목 | 상태 | 남은 작업 |
|---|---|---|
| D3 대표 PyTorch 학습 | PASS | 지원 범위는 위 고정 workload에 한정 |
| D5 passthrough 기능 | PASS | GPU 전환·native 기준·반복 일치·원복 확보 |
| D5 RPC·MPS 기준 | BLOCKED | 보존 방식의 같은 wheel/fixture 실행·정확성·계측 연결 미완료 |
| D6 공정 CPU/NUMA 배치 | NOT_RUN | 모든 방식의 guest/Worker/RPC/MPS CPU 집합 및 비용 고정 |
| D7 연산 제한 계약 | BLOCKED | 실제 limiter 관측 구간·허용오차·판정식을 확정해야 함 |
| 정식 E1 | BLOCKED | RPC·MPS와 전체 환경 동결 후 새 실험 버전으로 시작 |
| E2·E5 나머지 기능/장애 | BLOCKED | VM 합산 정책과 연속 학습 VM B, 격리 장애별 전체 반복 |
| E3·E4 성능 비교 | BLOCKED | 세 방식 calibration·5회 독립 반복·공유 fixed/window/latency 실행 |
| 실제 노드 단절·다중 GPU | NOT_RUN | 조건부 후속 실험 |

실행시간·samples/s가 metrics에 있어도 이번 correctness 구간의 수치를 성능 비교표로
사용하지 않는다. CPU pinning, 반복 횟수, warm-up 및 공통 측정 구간을 충족하지 않았다.
managed/registered host memory, PTDS, CUDA Graph, cuBLASLt, NCCL/DDP, 다중 장치,
다양한 optimizer나 PyTorch 전체 호환성은 이번 검증 범위 밖이다.

구체적인 빌드·VM 실행·passthrough 전환·비교 재현 순서는
[REPRODUCE_PYTORCH.md](REPRODUCE_PYTORCH.md), wire/API 범위는
[API_SUPPORT.md](../../runtime/shm/API_SUPPORT.md)에 정리했다.
