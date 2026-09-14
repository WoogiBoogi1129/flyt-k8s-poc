# 6단계: FLYT + HAMi End-to-End 실험 도구

**실험 도구 소스 구현 완료·검증 미실행(NOT_RUN).** 이 브랜치는
`stage/05-hami-backend`의 `63f93010479499003918f88f55917f55fd81067a`에서 분기했다.
빌드, 정적 검사, 패치 적용 검사, Python/CUDA 실행, 매니페스트 검증, 클러스터 배포,
GPU 실행을 수행하지 않았다. 문서의 명령은 향후 실행 절차이며 성공 기록이 아니다.

원래 계획의 6단계는 `VM → FLYT Client → RPC Server → HAMi → GPU` 경로의 실제
interception과 quota 검증이다. 이번 개발은 그 실험을 위한 실행기·probe·추적기·보고기를
제공한다. 도구를 작성했다는 이유로 이 검증 단계가 완료된 것은 아니다.

## 기존 단계와의 관계

- CUDA RPC·handle/pointer mapping 및 5단계 backend 패치는 그대로 보존한다.
- 4단계 CRD/Controller, 3단계 binding·Worker 세대 관리와 guest 설정 도구를 재사용한다.
- 새 CRD/Controller, HAMi 설치, GPU mode 변경, MPS daemon 조작을 추가하지 않는다.
- 기존 `scripts/run-pytorch-matrix.sh`는 공유 Cell 삭제·Manager 재시작 경로가 있으므로
  호출하지 않는다. 기존 19항목 CUDA/PyTorch 회귀는 후속 검증 대기로 남긴다.
- 여러 VM의 동시 실행·장애 격리·수명주기 검증과 SHM transport 교체는 이번 범위 밖이다.
  이번 aggregate 시험은 **한 Worker 안의 두 client**가 합산 quota를 공유하는지 다룬다.

## 구성과 실행 순서

| 파일 | 역할 |
|---|---|
| `run.py` | 기존 CRD에서 대상을 고정하고 probe와 수집기를 실행 |
| `probe.cu`, `kernel.cu`, `burn.cuh` | Runtime/Driver/async allocation, kernel, 메모리 경계와 compute 작업 |
| `inventory.py` | 선택한 Worker의 RPC PID·시작 tick·세대·라이브러리·바이너리 hash 수집 |
| `trace.py` | 선택한 RPC의 HAMi allocation/launch 함수 진입을 GDB로 수집 |
| `compare.py` | 두 quota의 반복 측정과 전체 사례 결과를 오프라인 비교 |
| `Containerfile`, `build.sh` | probe 산출물 및 5단계 기반 진단 Worker 이미지 |
| `config.example.json`, `conditions.example.json` | 대상 식별 및 비교 환경 관찰 입력 |
| `profile.example.yaml`, `request.example.yaml` | 불변 image 정책에 맞는 새 실험용 CR 예제 |

순서는 누적 패치/빌드 검증 → 환경 준비 → inspect → 단독 HAMi·guest 실험 →
두 quota 비교다. 과거 1~5단계의 NOT_RUN 항목을 이미 통과했다고 가정하지 않는다.

## 대상 고정과 실행 범위

실험 설정에 context, namespace, Worker/VMI/Pod/Request UID, GPU UUID를 명시한다.
실행기는 Ready·관찰 generation, VM/VMI owner, Profile 승인, Request snapshot,
Pod→ReplicaSet→Deployment→Worker 소유 관계, image digest와 할당량을 읽는다.
Worker generation·Manager epoch·Pod IP·container ID/restart count도 실행 전후와 진행 중
확인한다. 달라지면 실험을 무효화하고 다음 작업을 진행하지 않는다.

Pod exec 직전에 컨테이너의 `FLYT_POD_UID`를 다시 확인한다. Guest 연결은 명시한
context/namespace의 `vmi/<name>`를 사용하고 SSH host key 검증을 유지한다. 이 읽기들은
원자적 admission/분산 잠금이 아니므로 이미 실행 중인 원격 호출을 즉시 취소한다는 보장은 없다.
관찰된 변경은 결과를 BLOCKED로 만들며 원격 명령에는 별도 timeout을 둔다.

실험 시작 시 대상 Worker에 다른 RPC client가 있으면 중단한다. 신규 guest client마다
새 RPC PID가 정확히 하나 나타나는지 확인하고, 예상하지 않은 client가 들어오면 중단한다.
같은 실행 호스트에서는 Worker 단위 파일 잠금을 사용한다. 다른 실행 호스트나 사용자까지
잠그지는 않으므로 대상 VMI를 실험 전용으로 확보해야 한다.

실행기는 Secret을 읽거나 환경 전체를 덤프하지 않는다. 정해진 FLYT 식별·quota 환경변수와
MPS 설정 존재 여부만 기록한다. `inspect`도 Kubernetes API와 Worker `/proc`를 읽기 위한
`pods/exec` 권한이 필요하지만 CUDA probe나 tracer는 시작하지 않는다.

## Probe와 판정

Guest는 Cricket client 매핑, HAMi 부재, 직접 NVIDIA control device 부재, GPU UUID를
확인한다. Standalone은 같은 바이너리를 Worker 안에서 실행하며 HAMi 매핑·Cricket 부재를
확인한다. Guest client, probe 및 Driver용 PTX 파일은 실행 전 지정 SHA256과 비교한다.
이전 이미지의 `libcuda`/`libcudart` 설치·심볼릭 링크를 실행기가 자동 수정하지 않는다.

모든 probe는 초기화 후 READY에서 기다리고 실행기가 대상을 확인한 뒤 GO를 전송한다.
CUDA 명령은 probe 프로세스의 수명 안에서 수행한다. Runtime과 Driver 경로는 각각 별도로
할당·메모리 조회·kernel launch·결과 복사·해제를 사용한다. Kernel 결과는 별도 CPU 계산값과
비교한다. 단순 device count나 kernel 반환 코드만으로 작업 성공을 판정하지 않는다.

| 사례 | PASS 의미 |
|---|---|
| `standalone-memory` | FLYT 없이 동일 Worker에서 memory 경계·해제·재할당 성공 |
| `smoke-runtime`, `smoke-driver` | guest에서 해당 CUDA API 경로로 결과 검증 성공 |
| `trace-runtime`, `trace-driver` | 해당 guest smoke 성공과 일치하는 RPC의 HAMi allocation·launch 진입 확보 |
| `memory-runtime`, `memory-driver` | quota 근처까지 실제 할당/touch 성공, 초과에서 해당 CUDA OOM 반환, 해제 후 재할당 |
| `memory-async` | Runtime async allocation/free 및 synchronize를 포함한 같은 경계 검사 |
| `aggregate-memory` | A가 quota의 60%를 유지할 때 B는 잔여 경계에서 OOM, A 종료 후 새 client의 전체 경계 회복 |
| `compute-runtime`, `standalone-compute` | 반복 workload 결과가 정확함. **이 PASS만으로 compute 제한을 입증하지 않음** |

Memory 시험의 허용치는 사전에 지정한 headroom이다. 임의 OOM을 quota 성공으로 간주하지
않으며, quota를 우회해 계속 할당되더라도 최초 quota 초과 chunk에서 반복을 끝내 FAIL로
기록한다. 이 상한은 실험 자체의 추가 할당을 제한하는 것이며 안전한 GPU 소유권을 대신하지 않는다.
프로세스 종료·context overhead·async pool cache 영향은 실제 환경에서 검증해야 한다.

5단계의 엄격한 `0 < total <= configured quota` 초기화 조건은 유지한다. 정상 HAMi의
보고 방식과 맞지 않으면 초기화/실험이 실패할 수 있다. 조건을 자동 완화하거나 물리 메모리로
대체하여 PASS를 만들지 않는다. 해당 실패의 원인을 조사하고 별도 수정으로 남겨야 한다.

## 실제 interception 증거

`libvgpu.so`가 `/proc/maps`에 있다는 사실은 로드 증거다. `trace.py`는 이보다 강한 증거를
얻기 위해 해당 프로세스에 매핑된 HAMi ELF의 allocation 및 launch 함수 주소를 구하고,
그 주소에 breakpoint를 건다. GDB가 식별하는 라이브러리 경로도 일치해야 한다.
진입마다 run ID, RPC PID/start tick, HAMi hash, 함수명·주소를 기록한다. Guest 결과와
두 종류의 진입 증거가 모두 일치해야 trace 사례를 PASS로 기록한다.

지원하는 ELF는 일반적인 zero-based ET_DYN이고, 진입 심볼은 `cuMemAlloc[_v2]`,
`cuLaunchKernel[_ptsz]`다. 심볼 부재·다른 ELF 형태·GDB 부재·ptrace 거절은 BLOCKED다.
이 증거는 시험한 함수의 진입만 증명하며 모든 CUDA API, `cuGetProcAddress`의 모든 변형,
CUDA Graph/메모리 pool 경로까지 포괄하지 않는다. 실제 memory/compute 제한 증거도 별도로 필요하다.

**현재 Controller의 Worker capability에는 SYS_PTRACE가 없다.** GDB를 설치한 진단
이미지라도 노드 Yama/seccomp/ptrace 정책에 따라 attach가 거절될 수 있다. 도구는 capability,
Pod securityContext, sysctl, 노드 정책을 바꾸지 않는다. 허용된 별도 진단 환경이 없으면
interception 사례는 BLOCKED로 남는다. 이 제한을 해결하기 위해 전체 노드 보안을 낮추지 않는다.

Trace는 대상 RPC를 잠시 정지하므로 실험 전용 VMI의 smoke에만 사용한다. Allocation/launch
진입 후 또는 제한 시간 경과 후 detach하도록 작성했다. Compute 측정에서는 tracer를 사용하지
않는다. GDB 타이머/외부 timeout과 detach 동작 자체도 미검증이므로 초기 검증 항목에 포함한다.

## 향후 이미지·환경 준비 — 이번에는 실행하지 않음

1. 기본→2→3→5 패치 적용·C/Rust 빌드, 4단계 Controller/CRD 검증을 먼저 수행한다.
2. 5단계 Worker 이미지를 build/push하고 digest를 확정한다.
3. 아래 명령으로 probe와 진단 이미지를 만든다. 스크립트는 커밋된 HEAD만 사용하고 push하지 않는다.

```bash
./experiments/hami-e2e/build.sh probe-artifacts REGISTRY/flyt/e2e-probes:stage6
./experiments/hami-e2e/build.sh worker-diagnostic REGISTRY/flyt/worker:stage6 REGISTRY/flyt/worker-stage5@sha256:REPLACE
```

기본 아키텍처는 이전 이미지와 같은 `sm_120`, toolkit은 CUDA 12.8.1이다. Guest에 추출할
경로는 `/opt/flyt-e2e/`이며 probe·`burn.ptx`·SHA256SUMS를 포함한다. 동일 산출물을 Guest에
명시적으로 설치해야 한다. 기존 FLYT client와 CUDA driver proxy의 설치·게스트 연결은
이전 단계 절차를 따른다. Guest와 Worker에 CUDA 호환 라이브러리 및 Python 3가 필요하다.

4. 승인된 대상 GPU의 사용권·기존 HAMi 인프라·guest 라우팅을 확보한다. 외부 플랫폼의 사용
   해제는 이번 개발에서 확인하지 않았다. GPU 슬롯 번호 대신 확정 UUID를 사용한다.
5. 새 Profile/Request를 생성하고 실험 VM만 해당 Request를 선택한다. 예제 Profile은 미승인,
   image/UID는 placeholder다. 기존 공유 stage-5 ControlPlane은 재사용한다.
6. VM을 정상 stop/start해 새 VMI와 Worker를 생성한다. guest 설정은 해당 identity로 갱신한다.
   실행기가 CR 생성·승인·VM 재시작을 자동 수행하지 않는다.
7. `config.example.json`을 로컬 설정으로 복사하고 실제 UID·hash·키 경로·검증된 known_hosts를
   채운다. 낮은/높은 quota 모두 실험할 경우 Profile 상한도 두 요청을 수용해야 한다.

## 향후 실행과 비교 — 이번에는 실행하지 않음

```bash
# API/proc 읽기만 수행. 출력 폴더는 새 경로여야 한다.
python3 experiments/hami-e2e/run.py inspect --config .local/stage6-low.json --output results/stage6-inspect

# 실제 CUDA 실행. --execute 없이는 run 명령을 거절한다.
python3 experiments/hami-e2e/run.py run --execute --config .local/stage6-low.json --output results/stage6-low
```

기본 실행은 모든 사례를 선택한다. `--cases smoke-runtime memory-runtime`처럼 일부만 선택할
수 있지만 나머지는 NOT_RUN이므로 전체 PASS가 되지 않는다. FAIL/BLOCKED가 발생하면
다음 사례를 중단한다. 실패를 지우고 이어 붙이는 resume/결과 합치기는 제공하지 않는다.
준비를 보완한 뒤 새 결과 폴더로 전체 실행을 다시 수행한다.

Compute 비교는 예를 들어 30%와 100%를 **동시에 실행하지 않고** 같은 GPU에서 순서대로
측정한다. Request compute 변경 후 정상적인 새 VMI 생성 절차를 거치고 UID 설정을 갱신한다.
memory, image, guest client/probe/PTX, 시간·반복 횟수는 동일하게 유지한다. GPU clock/power
정책이나 다른 workload를 변경해서 결과를 맞추지 않는다. 다른 GPU의 설정도 변경하지 않는다.

각 quota에서 3회 이상 측정하며, 고정 geometry와 warmup 후 host wall time으로 처리량을
계산한다. 비교 threshold는 실행 전에 정한다. quota 비율이 처리량 비율과 정확히 같다고
가정하지 않는다. `conditions.example.json`에는 두 report의 hash와 동일 driver·다른 workload
부재·compute 중 tracer 부재·clock/power 설정 불변을 관찰한 근거 파일을 지정한다.
비교기는 이를 **운영자가 제공한 관찰**로 표시하며 직접 측정했다고 표현하지 않는다.

```bash
python3 experiments/hami-e2e/compare.py \
  --low results/stage6-low/report.json --high results/stage6-high/report.json \
  --max-throughput-ratio 0.75 --max-relative-range 0.30 \
  --conditions .local/stage6-conditions.json --output results/stage6-comparison.json
```

`0.75`는 사용법 예시이며 검증된 임계값이 아니다. 반복 측정의 변동 폭이 허용치를 넘거나
환경 증거가 없으면 BLOCKED다. 두 경로의 low/high 처리량이 사전 임계값을 충족해야 compute
제한을 PASS로 판단한다. 모든 기본 사례까지 두 실행에서 PASS인 경우에만 비교 보고서의
전체 상태를 PASS로 만든다. 이는 해당 GPU·바이너리·workload 조합에 한정한 결과다.

## 결과와 종료

결과에는 `report.json`, 시작/종료 inventory, 각 probe 로그 및 RPC inventory, trace 로그가
포함된다. 비밀키·Secret·전체 환경은 복사하지 않는다. `inspect` 성공은 exit 0/NOT_RUN,
`run`은 비교 미완료 때문에 모든 사례가 성공해도 전체 BLOCKED/exit 2다. FAIL은 exit 1,
실행 불가·증거 부족은 BLOCKED/exit 2, 선택하지 않은 시험은 NOT_RUN이다. `compare.py`는
전체 조건을 충족한 경우 exit 0/PASS를 낸다.

사례 사이에 기존 Node Manager의 정상 RPC 회수를 기다린다. 시간 내 정리되지 않으면 중단하고
전체 Manager 재시작·RPC PID kill·IPC 일괄 삭제·HAMi cache 삭제를 실행하지 않는다.
실행기 종료 시 자신이 시작한 로컬 연결을 닫으며 원격 probe에는 150초(+강제 종료 5초),
tracer에는 별도 제한 시간이 있다. 네트워크 단절 시 원격 작업이 즉시 종료된다고 보장하지 않는다.
남은 대상 세션의 정상 정리 상태를 확인하기 전에는 다음 실험을 시작하지 않는다.

현재 모든 도구의 동작과 위 절차는 **NOT_RUN**이다. `versions.json`은 개발 시점의 상태이며
실험 실행으로 덮어쓰지 않는다. 이후 실험 보고서를 별도 근거로 보존한다.
