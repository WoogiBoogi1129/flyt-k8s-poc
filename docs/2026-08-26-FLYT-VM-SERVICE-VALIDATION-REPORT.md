# Flyt 기반 Kubernetes GPU VM 서비스 검증 실험 보고서

## 1. 요약

- 실험 일자: 2026-08-26 (UTC)
- 대상 commit: `9299ef9c149573ee3aaffe026847124e04abbacd`
  위의 로컬 미커밋 Kubernetes/whole-GPU 변경 포함
- 대상: KubeVirt VM 2대, Flyt Cluster Manager/GPU Cell, 물리 GPU 1번
- 자원 모델: 물리 GPU 한 개를 Flyt가 VM별 46 SM/8192 MiB로 논리 분할
- 결론: **제한적인 GPU VM PoC로는 성립하지만 범용 GPU VM 서비스로는 아직
  부적합하다.** 일반 VM 환경과 PyTorch 기본 eager 학습 경로는 동작하지만,
  CUDA API 호환성과 비정상 종료 회수 및 영속 VM 디스크가 주요 차단점이다.

다른 namespace의 workload, GPU 0의 기존 MIG claim, GPU 2는 변경하지 않았다.
원시 결과는 `results/20260826-flyt-vm-service-validation/`에 저장했다.

## 2. 실험 환경

| 항목 | 값 |
|---|---|
| Kubernetes | v1.34.3 server |
| KubeVirt | v1.8.4 |
| Guest | Ubuntu 22.04.5 LTS, kernel 5.15.0-186 |
| VM | VM A/B, 각 4 vCPU, 8 GiB RAM |
| GPU | NVIDIA RTX PRO 6000 Blackwell Server Edition |
| GPU UUID | `GPU-7d708c42-8d4a-16d5-0746-474567157aa3` |
| GPU mode | whole GPU, MIG disabled |
| Flyt quota | VM별 46 SM, 8192 MiB |
| PyTorch | 2.11.0+flyt.cu128, CUDA 12.8, sm_120 |

GPU 1의 DRA whole-device claim만 Flyt가 사용했다. GPU 0에는 기존
`test-jupyter-nb` claim이 있었으며 이를 변경하지 않았고 GPU 2의 메모리 사용량은
실험 전후 0 MiB였다.

## 3. 결과 요약

| ID | 검증 항목 | 판정 | 핵심 증거 |
|---|---|---|---|
| E0 | 공유 클러스터 안전 Gate | PASS | GPU 1만 Flyt가 사용, 타 claim 유지 |
| E1 | SSH/OS/network/guest reboot | PASS | VM A/B SSH, DNS, HTTPS, marker 유지 |
| E2 | 사용자 개발환경 | PARTIAL | apt·Python venv·JupyterLab 4.6.3 성공 |
| E3 | 기본 Flyt CUDA | PASS | VM A/B device/compute/8 GiB OOM 경계 성공 |
| E4/E5 | PyTorch/CUDA 호환성 | PARTIAL | VM별 19항목 중 10 PASS, 9 FAIL/CRASH |
| E6 | ML workload | PARTIAL | tensor/autograd/optimizer/AMP 성공, CNN 실패 |
| E7 | nested container | BLOCKED-INFRA | 2 GiB root disk 여유 부족으로 Docker 설치 불가 |
| E8 | 두 VM 동시 실행 | PASS | 동시 46 SM/8 GiB, 두 process rc=0, 정상 회수 |
| E9 | SIGKILL 회수 | FAIL | 30초 후에도 stale virtual server active |
| E9-R | 제한 복구 후 회귀 | PASS | Flyt 전용 runtime 복구 후 VM A/B 기본 시험 통과 |

## 4. E1-E2: VM 환경 자유도

두 VM 모두 일반 사용자 `experiment`로 SSH 접속했고 `apt`를 통한 `tree`
패키지 설치, 외부 DNS/HTTPS 접근, Python 3.10 실행에 성공했다. VM 내부 guest
reboot 후 설치 파일과 marker, Flyt client manager가 유지됐다.

VM A의 `/opt/flyt-pytorch` 20 GiB 보조 디스크에 Python venv와 JupyterLab
4.6.3을 설치했다. 인증 token을 포함한 `/api/status` 호출이 성공해 실제 서버
기동까지 확인했다. 첫 시도에서 HTTP 403을 서버 장애로 오판했으나 token을
포함한 재시험으로 시험 방법을 수정했다.

### 확인된 저장장치 문제

1. root disk가 2 GiB이며 Flyt/PyTorch 설치 후 여유 공간이 약 30 MiB였다.
2. `/opt/flyt-pytorch`는 20 GiB지만 `emptyDisk`이므로 VMI 삭제 또는 VM
   stop/start 시 사용자 환경 보존을 보장하지 않는다.
3. guest reboot 후 보조 ext4 filesystem의 mount root 소유권이 `root:root`로
   돌아와 일반 사용자의 설치가 거부됐다. 수동 `chown` 후 설치는 성공했다.

따라서 이번 결과는 guest reboot 동안의 유지만 입증하며, cloud VM에서 기대하는
instance stop/start 영속성은 입증하지 않는다.

### 해결 방안

- Ubuntu cloud image를 DataVolume으로 import/clone하고 30 GiB 이상의 영속
  root PVC를 사용한다.
- 사용자 데이터 디스크도 `emptyDisk` 대신 VM별 PVC로 변경한다.
- mount 이후 ownership을 복구하는 systemd oneshot 또는 tmpfiles 규칙을 넣는다.
- stop/start 및 VMI 재생성 뒤 package, venv, notebook checksum을 재검증한다.

Conda와 VS Code Server는 저장공간 Gate 때문에 설치하지 않았다. 이는 Flyt API
호환성 실패가 아니라 현재 VM disk profile의 인프라 제약이다.

## 5. E3: 기본 CUDA 회귀

두 VM에서 다음 결과가 동일하게 통과했다.

- CUDA device count: 1
- guest에 `/dev/nvidia0` 미노출
- Flyt가 보고한 SM: 46
- compute checksum: 기대값과 일치
- 8192 MiB 할당 성공
- 다음 256 MiB 할당은 expected OOM
- 정상 종료 후 active client/virtual server 없음

## 6. E4-E6: PyTorch 및 CUDA API 경계

두 VM에서 같은 결과를 재현했다.

| 항목 | 결과 | 해석 |
|---|---|---|
| environment | PASS | CUDA 12.8, Blackwell 인식 |
| tensor runtime | PASS | 기본 tensor/kernel 동작 |
| autograd/optimizer | PASS | 작은 MLP 학습 경로 동작 |
| cuBLAS | PASS | matrix multiplication 동작 |
| AMP FP16/BF16 | PASS | mixed precision 기본 경로 동작 |
| allocator | PASS | 기본 caching allocator 동작 |
| serialization/RNG | PASS | 기본 framework 기능 동작 |
| stream/event | PASS | stream 및 event 기본 경로 동작 |
| pinned memory | PASS | pinned host memory 동작 |
| FFT | FAIL | `CUFFT_INTERNAL_ERROR` |
| sparse | FAIL | `cuMemAddressReserve` symbol 없음 |
| linalg | FAIL | `libtorch_cuda_linalg.so` dlopen 실패 |
| compile modes | PARTIAL/FAIL | eager 성공, Triton 미설치로 inductor 불가 |
| cudaMallocAsync | FAIL | `cudaDeviceGetDefaultMemPool Not implemented` |
| CUDA Graph | FAIL/CRASH | `capture_begin`에서 SIGSEGV |
| CNN models | FAIL | cuDNN engine 선택 실패 |
| cuDNN | FAIL | backend attribute/finalize 직렬화 오류 |
| SDPA backward | FAIL | binary architecture 내부 assertion |

항목 기준 성공률은 VM별 10/19(52.6%)다. `compile_modes`는 eager subcase만
성공했으므로 전체 항목은 FAIL로 계산했다.

추가로 PyTorch `get_device_properties()`는 Flyt quota인 46 SM/8 GiB가 아니라
물리 GPU의 188 SM/101973819392 bytes를 노출했다. native Flyt probe와 실제
resource document는 46 SM/8 GiB였으므로 **resource enforcement와 framework
property disclosure가 일치하지 않는다.** 사용자가 capacity planning을 할 때
오해할 수 있어 별도 수정이 필요하다.

### 원인과 해결 우선순위

1. **P0 Driver VMM 구현:** `cuMemAddressReserve`, map/unmap, access, mempool 계열
   API를 RPC protocol과 server/client 양쪽에 구현한다.
2. **P0 CUDA Graph 안전화:** 미지원 graph API는 NULL/잘못된 handle 대신 명시적
   error를 반환하고, capture lifecycle 직렬화 및 handle mapping을 구현한다.
3. **P0 cuDNN backend:** descriptor attribute의 type/count/size 직렬화와 backend
   engine lifecycle을 수정한다.
4. **P1 동적 library forwarding:** cuFFT, CUDA linalg 등 companion library의
   loading과 symbol forwarding을 패키징한다.
5. **P1 property virtualization:** PyTorch가 읽는 device property도 Flyt quota에
   맞추거나 physical capacity와 allocated quota를 별도 API로 명확히 제공한다.
6. **P2 Triton:** sm_120 호환 Triton을 version pin한 뒤 inductor를 재시험한다.
7. 각 수정 후 현재 subsystem matrix를 regression test로 사용한다.

TensorFlow는 Flyt 호환 빌드/patch와 version-pinned artifact가 없어 실행하지
않았다. 일반 TensorFlow wheel의 설치 성공만으로 Flyt GPU 호환성을 주장할 수
없으므로, PyTorch와 동일하게 CUDA symbol audit와 전용 compatibility matrix를
먼저 준비해야 한다.

## 7. E7: Nested container

Docker 설치 전 VM A root disk 여유가 30 MiB였다. `apt update`가 package index를
기록하지 못해 Docker 설치를 중단했고, 불완전한 apt index를 제거했다. Docker
daemon이나 container workload는 생성되지 않았다.

따라서 nested container GPU 사용 여부는 **미검증**이다. 정확한 재시험 조건은
다음과 같다.

- 영속 root PVC 30 GiB 이상
- Docker data-root를 별도 사용자 PVC에 배치
- container에 Flyt library, client socket, 필요한 System V IPC를 명시적으로 전달
- `/dev/nvidia*` 또는 `--gpus all`에 의존하지 않는 시험
- container 내부 native probe를 먼저 실행한 뒤 framework test 수행

## 8. E8: 멀티테넌트 동시 실행

두 VM에서 15초 bounded native CUDA workload를 동시에 실행했다.

| 항목 | VM A | VM B |
|---|---:|---:|
| client active | true | true |
| SM | 46 | 46 |
| memory | 8589934592 | 8589934592 |
| iteration records | 680 | 683 |
| process exit | 0 | 0 |

두 virtual server가 동시에 존재하는 것을 Cluster Manager에서 확인했다. 두
process 모두 정상 종료했고 5초 뒤 active client가 0개로 회수됐다. 이는 짧은
bounded native workload에 대한 기능 증거이며 장기 안정성 또는 성능 공정성의
증거는 아니다.

## 9. E9: 비정상 종료와 회수

VM A의 120초 workload를 시작한 뒤 systemd를 통해 SIGKILL했다. process는
`Result=signal`, `ExecMainStatus=9`로 종료됐지만 30초 뒤에도 다음 상태가 남았다.

- VM A client: active
- SM: 46
- memory: 8589934592
- virtual server RPC allocation: 유지

VM B의 client manager는 계속 active였으므로 guest 간 OS isolation은 유지됐다.
그러나 Flyt가 비정상 종료를 감지해 자원을 회수하지 못했으므로 production
lifecycle 요구사항은 실패했다.

Flyt GPU Cell과 Cluster Manager만 재시작하고 두 guest agent를 재연결하여
복구했다. 복구 후 VM A/B의 기본 CUDA/8 GiB quota 시험이 모두 다시 통과했고
stale allocation은 제거됐다.

### 해결 방안

- client heartbeat와 lease TTL을 Cluster Manager에 추가한다.
- TCP/RPC disconnect, VM deletion event, client manager restart에 대해 idempotent
  allocation cleanup을 구현한다.
- Kubernetes controller가 VMI UID와 Flyt client allocation을 매핑하고 VMI 종료
  finalizer에서 release를 요청한다.
- TTL 후 server-side 강제 회수와 GPU Cell process 정리를 구현한다.
- SIGTERM/SIGKILL, VM stop/delete, node/network failure를 CI fault matrix로 만든다.

## 10. 연구 질문 판정

### RQ1: 사용자 정의 AI/ML 환경을 보존하는 fractional GPU VM인가?

**부분적으로 가능하다.** apt, venv, Jupyter, custom PyTorch와 기본 학습 경로는
동작한다. 그러나 현재 디스크가 영속 instance storage가 아니고 PyTorch
호환성도 제한적이다.

### RQ2: CUDA API remoting의 호환성 한계는 어디인가?

기본 Runtime/cuBLAS/autograd/stream은 동작하지만 Driver VMM, mempool, CUDA
Graph, cuDNN backend, FFT/linalg 및 일부 architecture-sensitive workload가
경계다. 항목 성공률은 52.6%였다.

### 최종 Gate

현재 Flyt 시스템은 **Native CUDA와 제한된 PyTorch eager workload를 제공하는
연구용 GPU VM PoC**로 평가한다. 임의의 최신 framework/container를 설치할 수
있는 public-cloud형 범용 GPU VM 또는 production multi-tenant service라고
평가해서는 안 된다.

다음 Gate는 영속 VM disk, P0 CUDA API, stale allocation TTL을 구현한 뒤 동일
실험을 반복해 통과시키는 것이다. Incus 및 성능 비교는 이 기능 Gate 이후에
진행한다.

## 11. 증적 위치

- 안전 기준선: `raw/e0-safety-baseline.txt`
- VM/패키지/Jupyter: `raw/e1-*`, `raw/e2-*`
- 기본 CUDA: `e3-basic/`
- PyTorch matrix: `e4-pytorch-matrix/`
- 동시 실행: `raw/e8-*`
- SIGKILL 및 복구: `raw/e9-*`, `e9-post-recovery-basic/`
- 전체 경로: `results/20260826-flyt-vm-service-validation/`

`raw/e7-docker-install.txt`는 apt가 disk-full 상태에서 같은 warning을 반복해
크기가 커졌으므로 실패 원인 확인용 원시 자료로만 보존한다.

## 12. 2026-08-27 수정 후 재검증

### 적용한 수정

1. CUDA Graph capture 진입점을 fail-closed 처리해 미구현 원격 Graph API가
   guest stream handle을 host CUDA library에 전달하지 않게 했다.
2. 주석 처리돼 있던 client process monitor를 활성화했다.
3. monitor 설정 키를 실제 Rust lookup과 같은 `process_monitor_period`로 고치고
   10초로 설정했다.
4. 죽은 client ping 대기를 350초에서 3초로 제한하고 send 실패를 비활성으로
   처리했다.
5. Cluster Manager의 zero-client 처리에서 같은 `clients` mutex를 중첩 잠금하던
   교착을 제거했다.
6. guest 설치 시 apt cache를 정리하고 crash dump를 보조 디스크로 보존하며
   `/opt/flyt-pytorch` 소유권을 복구하도록 했다.

### 재검증 결과

| 항목 | 수정 전 | 수정 후 | 판정 |
|---|---|---|---|
| native CUDA compute | PASS | SM 46, checksum 일치, rc=0 | PASS 유지 |
| CUDA Graph | `capture_begin` SIGSEGV/core dump | 0.126초에 `CUDA error: Not supported`, 정상 deinit | 안전성 수정 PASS |
| SIGKILL client 감지 | 30초 후에도 active | client manager가 closed client 감지 | PASS |
| SIGKILL 자원 회수 | stale allocation 유지 | kill 후 6초에 `list-vms`가 빈 목록 | PASS |
| Cluster Manager 응답 | 회수 경로 미사용 | deadlock 수정 후 CLI 계속 응답 | PASS |

첫 회수 패치만 적용했을 때는 monitor가 350초 ping timeout에 막혔고, timeout을
3초로 줄인 뒤에는 Cluster Manager의 zero-client mutex 재진입 교착이 드러났다.
두 원인을 순차 수정한 최종 빌드에서 6초 회수를 확인했다. 이 수치는 단일 시행의
관측값이며 최악 시간 보장은 `monitor period 10초 + ping timeout 3초 + 처리 시간`이다.

VM A의 19항목 회귀 행렬 결과는 기존과 동일하게 10 PASS/9 FAIL이다. Graph의
기능 상태는 여전히 FAIL이지만 process crash가 명시적 미지원 오류로 바뀌었다.
VM B 반복 행렬은 root containerDisk가 100%가 되어 `/tmp`를 생성할 수 없었으므로
중단했다. apt/snap의 재생성 가능한 cache를 정리해 213 MiB를 확보한 뒤 VM B의
native compute와 정상 회수는 다시 PASS했다. 따라서 이 중단은 Flyt CUDA 회귀가
아니라 기존 2 GiB root disk 차단점의 재현으로 판정한다.

### 남은 차단점

- Graph 기능 자체는 구현되지 않았으며 현재 수정은 crash 방지 조치다.
- Driver VMM/mempool, cuFFT, linalg, cuDNN backend, SDPA, Triton은 미지원 상태다.
- 클러스터에 DataVolume CRD가 없어 root image의 영속 PVC import를 바로 적용할 수
  없다. CDI를 설치할 권한·운영 합의가 생기기 전에는 30 GiB 이상 root image를
  제공하는 별도 containerDisk가 필요하다.
- 사용자 디스크는 VM별 PVC로 전환하고 mount 후 ownership을 복구해야 한다.

재검증 원시는 `results/20260827-flyt-fixes/`에 저장했다. 핵심 파일은
`compute-smoke.txt`, `cuda-graph.jsonl`, `sigkill-reaper-fixed.txt`,
`client-manager-reaper.log`, `full-matrix/`이다.

## 13. 2026-08-31 Track A2 최종 재검증

이 절은 8월 26~27일의 호환성 판정을 대체한다. 상용 NVIDIA vGPU는 유료
라이선스가 필요하므로 설계와 실험 범위에서 제외했다. 최종 구조는 KubeVirt VM에
물리 GPU를 직접 노출하지 않고, DRA로 전체 GPU 1개를 받은 GPU Cell Pod가 CUDA
MPS와 Flyt RPC를 통해 VM별 46 SM/8 GiB 논리 자원을 제공하는 Track A2다.

### 최종 산출물

| 항목 | SHA-256 |
|---|---|
| 적용된 Flyt source diff | `6067a74bb5984786f49135a125a7a860388f324e5ae6f4ca7b104c334c22b1de` |
| `cricket-rpc-server` | `bf9bbb4f47cd792054cf04b4e2156c637131b2af8c12ba5db9ca3882c77ca12b` |
| `cricket-client.so` | `fbd3d25d73faef4eccdb530d0520bfe05e8e39c3af33a95be43657257429adb2` |
| guest bundle | `40306399e1a70b1fe957a154f2102e71dd670980af2cfe59e3c8df62ab7ab524` |
| bundled `cuobjdump` | `9d515ac6f60c246ef63fe87b0a95d209b00ff3e25b716f8f263520010760e8f8` |

패치 적용 재현성, shell/Python 정적 검사, 크기·공개 ABI gate와 매니페스트
렌더링은 모두 PASS했다. 임시 CUDA Graph 진단 패치는 최종 series에서 제거했다.

### PyTorch 19항목 결과

| VM | 최초 결과 | 보정 후 최종 결과 |
|---|---:|---:|
| VM A | 19/19 PASS | 19/19 PASS |
| VM B | 16/19 PASS | 19/19 PASS |

VM B의 최초 세 실패와 해결은 다음과 같다.

1. `models`, `cudnn`: VM B 런처에 `TORCH_CUDNN_V8_API_DISABLED=1`이 없어 미완성
   cuDNN v8 backend 경로가 선택됐다. 검증된 typed legacy cuDNN 경로를 기본으로
   선택해 두 항목을 통과시켰다.
2. `compile_modes`: Triton 3.7.1, C compiler, Python 개발 헤더가 차례로 누락돼
   Inductor가 실패했다. 큰 Python 패키지와 재배치형 GCC/sysroot를 20 GiB 실험
   디스크에 설치하고 런처가 `CC`, include/library path를 구성하도록 했다.
3. 컴파일 완료 뒤에는 Flyt client가 Triton CUBIN 분석용 `cuobjdump`를 찾지 못했다.
   guest bundle의 고정 도구를 `/opt/flyt-client/bin`에 설치해 해결했다.

최종 `compile_modes`는 `eager=pass`, `inductor=pass`이고, VM B 합산 결과는
`results/20260831T114000Z-a2-vm-b-compile-remediation3/combined-summary.tsv`에
19 PASS/0 FAIL/0 missing으로 저장했다. VM A 전체 행렬은
`results/20260831T061000Z-a2-vm-a-full19/summary.tsv`에 있다.

### CUDA Graph와 다중 VM

typed capture begin/end, graph instantiate/launch/destroy, capture-info와
`cudaGraphGetNodes` RPC를 구현했다. ONC RPC worker thread가 요청 사이에 달라질 수
있으므로 서버 capture는 RELAXED mode를 사용하고, guest의 thread-local capture
mode는 client TLS로 유지한다. 명시적 stream에서 메모리를 사전 할당하고 cuBLAS와
pointwise kernel을 warm-up한 뒤 capture/replay하는 최종 시험은 두 VM 모두
다음 동일 결과로 PASS했다.

- first checksum: `13062.025390625`
- second checksum: `1624.298095703125`
- 증적: `results/20260831T120000Z-a2-final-graph-ab/`

15초 bounded 동시 부하에서는 VM A/B가 각각 별도 virtual-server RPC ID 3/4,
46 SM, 8 GiB를 할당받았다. GPU Cell에서 별도 `cricket-rpc-server` 두 개와 하나의
MPS server를 관측했고 두 workload 모두 rc=0으로 끝났다. 종료 8초 뒤
`list-vms`는 빈 목록이었다. 증적은
`results/20260831T122000Z-a2-final-multi-vm/`에 있다.

### 개발 VM 저장공간 보정

2 GiB root에 대형 guest bundle을 남겨 VM A 런처 갱신이 실패한 사례가 있었다.
재생성 가능한 Flyt bundle과 APT cache/index만 제거해 69 MiB를 확보하고 런처를
복원했다. 설치 스크립트는 이후 bundle과 wheel을 `/tmp`의 20 GiB 실험 디스크에서
사용하고 종료 시 삭제하며, Triton과 relocatable toolchain도 같은 디스크에 둔다.

### 최종 판정과 남은 범위

계획한 단일 VM PyTorch 19항목, CUDA Graph, bounded 두-VM 동시 실행은 모두
성공했다. 이는 현재 version-pinned PyTorch/CUDA 12.8 실험 프로파일에 대한
호환성 결과이며 모든 CUDA API의 완전 호환이나 production tenant isolation을
뜻하지 않는다. multi-GPU/NCCL, 장기 공정성·성능, cuDNN v8 backend descriptor,
VM stop/start 후 사용자 디스크 영속성과 network/node 장애 fault matrix는 후속
gate다. 특히 일반 개발 VM을 위해 root 및 사용자 디스크를 VM별 영속 PVC로
확대하는 작업은 여전히 필요하다.
