# 7단계: HAMi 세션 관리 전용 Control Plane

**소스 구현 완료·검증 미실행(NOT_RUN).** 개발 기준은 `stage/06-hami-e2e`의
`c960d13bf729085d6226311b7175b2a91da22798`, 브랜치는 `stage/07-session-control-plane`이다.
패치 적용 검사, 의존성 해결, 빌드, 정적 검사, 프로토콜 시험, 매니페스트 검증, GPU 실행과
실제 배포를 수행하지 않았다. 아래 명령과 동작 설명은 작성한 구현과 향후 절차다.

첨부 원문의 요약표와 본문은 7단계 번호가 다르다. 이번 작업은 검토에서 정한 **본문 7단계,
FLYT Cluster Manager의 자원 관리 역할 축소**다. Multi-VM/Lifecycle 검증 성공을 뜻하지 않는다.
6단계 역시 실험 도구만 작성됐고 interception/quota 실험은 NOT_RUN이다.

## 변경한 책임

5단계는 기존 Manager의 GPU·VMResources·VirtServer 구조를 유지한 채 HAMi에서 MongoDB
접근과 quota 연산을 우회했다. 7단계는 별도 Rust 실행 경로에서 binding, Worker 등록,
client 세션과 RPC endpoint만 관리한다.

| 책임 | 관리 위치 |
|---|---|
| 승인 GPU UUID와 실행 환경 | `FlytGPUProfile` |
| VM 요청·quota·VMI별 snapshot | `FlytGPURequest`, 기존 Controller |
| Pod 생성·할당·수명주기 | `FlytWorker`, Controller와 HAMi |
| 실제 memory/core 제한 | Worker의 HAMi/CUDA 경로 |
| VM/Worker binding, RPC endpoint, 세션 상태 | 새 HAMi Manager |
| CUDA 호출·pointer/handle mapping | 기존 FLYT/Cricket RPC |

Manager는 quota를 복사해서 다시 승인하거나 placement를 계산하지 않는다. 기존 Node Manager
프로토콜의 `0,0,0` 생성 필드는 유지하되 별도 GPU quota 장부에는 저장하지 않는다. 상태 조회의
`resource_authority`는 Kubernetes/HAMi이며 실제 요청량은 해당 CRD를 조회해야 한다.

## 코드와 패키징

| 소스 | 역할 |
|---|---|
| `model.rs` | Binding, WorkerKey, ClientKey, Session과 상태 |
| `manager.rs` | binding API, 세션 생성·정리, 조회 서비스 |
| `worker.rs` | Worker별 직렬 제어 채널, 생성·해제·등록 확인 |
| `wire.rs` | 제한된 JSON/행 프레임, deadline, 식별자 생성 |
| `guest.rs`, `guest_main.rs` | Guest 전용 세션 연결·heartbeat·종료 |
| `cli.rs` | 세션 중심 JSON 조회 CLI |

실행 패치는 `patches/0001-session-control-plane.patch`다. `src/`를 수정한 뒤에는
`author-patch.py --source PATH`로 패치도 갱신해야 한다. 이 스크립트는 diff를 작성하며
patch apply/build/test를 실행하지 않는다. authoring 입력은 baseline 패치가 반영된 Flyt 소스다.
수정하는 기존 두 파일 Cargo.toml·Guest IPC handler에는 2·3·5단계의 추가 변경이 없다.
이번에는 기존 로컬 authoring 소스에서 diff를 작성했으며, 누적 패치 적용은 확인하지 않았다.

패치 순서는 **기본 → 2 → 3 → 5 → 7**이다. 4·6단계에는 이 runtime의 patch가 없다.
기존 1~6단계 소스/패치, 기본 `patches/series`, Go Controller/CRD를 수정하지 않았다.

Cargo의 `mongodb`를 optional dependency로 바꾸고 `legacy-mps` feature에 묶었다.
기본 feature는 legacy를 유지한다. 7단계 이미지는 `--no-default-features --features
hami-control-plane`로 다음 binary만 선택해 빌드하도록 했다.

- `flyt-session-manager` → 이미지의 기존 경로 `flyt-cluster-manager`
- `flyt-session-client-manager` → Guest 산출물의 기존 경로 `flyt-client-manager`
- `flyt-sessionctl` → 이미지의 `flytctl`
- 기존 `flyt-node-manager`

HAMi binary에 legacy feature를 함께 활성화하면 컴파일 오류를 내도록 작성했다. 기존
MongoDB/placement/scaling Manager 모듈은 새 Manager에서 import하지 않는다. Guest의
옛 resource metrics/scaling monitor도 실행 경로에서 제외한다. `flytctlnet`은 7단계 이미지에
포함하지 않는다. 실제 의존성 그래프와 컴파일 결과는 **미확인**이며 Cargo lock 재해결도 하지 않았다.

CPU RPC build는 5단계의 `FLYT_BUILD_BACKEND=hami`를 유지한다. CUDA API 구현·context·
pointer mapping을 변경하지 않는다. 단계 metadata `STAGE7.json`, `STAGE7_PATCHES.sha256`을
각 이미지에 포함하도록 했다. legacy 실행 재현은 보존된 이전 브랜치를 기준으로 한다.

## 세션 식별과 정리

Manager는 session ID를 생성하여 세션 기본 키로 사용한다. 보조 키에는 VMI/Worker/Pod UID,
Worker generation, Guest Client Manager의 instance nonce, client GID가 들어간다. IP는
binding과 통신 peer를 대조하기 위한 주소이며 세션 기본 키로 사용하지 않는다.

새 세션은 Starting → Active → Closing으로 진행한다. 동일 client key의 활성 연결을
자동 재사용하지 않는다. 이전 세션이 Closing으로 전환된 뒤 같은 GID로 새 세션을 생성해도
지연 cleanup은 이전 session ID와 RPC ID만 처리하도록 했다. 보조 인덱스 삭제에도 session ID를
비교한다. 기존 세션의 delayed cleanup을 새 세션으로 옮기지 않는다.

Guest는 CUDA client마다 별도의 제어 TCP 연결을 유지한다. deinit 시 그 연결만 닫으며,
프로세스 종료는 `/proc/PID/stat`의 시작 tick을 비교해 GID별로 회수한다. PID가 같아도 시작
tick이 다르면 이전 client로 취급한다. 검사 후 제거할 때도 PID/GID/start tick이 일치해야 한다.
client에 RPC endpoint를 전달하지 못하면 생성한 제어 세션을 닫도록 기존 IPC handler를 보완했다.

Guest의 local SysV 메시지 자체에 nonce를 추가한 것은 아니다. 로컬 IPC 접근은 기존의 신뢰
전제를 유지하며 PID/start tick 확인을 모든 IPC replay 방지나 악성 guest 격리로 해석하지 않는다.

Heartbeat는 Guest에서 5초 주기, Manager 읽기 대기는 30초다. EOF·timeout 이후 10초 유예를
두고 해당 RPC를 정리한다. Worker I/O 대기 및 실제 CUDA 종료 시간이 더해질 수 있으므로
정확히 10초 안에 GPU 메모리가 회수된다는 보장은 아니다.

Worker 통신은 Worker별 mutex로 직렬화하며 Manager 전체 상태 lock을 잡은 채 네트워크
응답을 기다리지 않는다. cleanup 작업은 Worker당 하나만 진행한다. 연결 수와 세션 수에는
정적인 운영 상한을 두지만 이는 GPU quota 계산이 아니다. 더 낮은 실제 client 한도는 기존
Node Manager의 `FLYT_MAX_CLIENTS`가 적용한다.

할당/정리 응답이 유실되거나 형식이 잘못되어 결과를 확정할 수 없으면 해당 Worker 연결을
종료한다. 이때 그 Worker의 다른 client도 종료될 수 있다. 다른 Worker를 일괄 재시작하거나
GPU reset으로 복구하지 않는다. 새 구조도 Manager crash, Worker restart 이후 CUDA 세션의
투명 복구를 제공하지 않는다.

## 프로토콜과 Controller 호환성

[제어 프로토콜 문서](PROTOCOL.md)에 요청/응답과 상태 전이를 정리했다.

- 기존 Controller binding v3의 `observe/bind/unbind/list`와 Node Manager v3 hello를 유지한다.
- 응답의 `epoch`를 유지하고 `session_protocol`을 추가한다. 4단계 Controller/CRD를 재사용한다.
- Guest 제어 연결은 **`flyt-session-v7` JSON 프로토콜로 변경**한다. 7단계 Guest Client Manager가 필요하다.
- CUDA ONC RPC와 C client IPC 응답 `address,rpc_id`는 유지한다.
- 이전 Manager 제어 CSV와 `ZERO_VCUDA_CLIENTS`, 자원 변경/migration 명령은 받아들이지 않는다.
- CLI는 `list-bindings`, `list-workers`, `list-sessions`의 versioned JSON만 제공한다.

구형 Guest와 신형 Manager 또는 그 반대 조합은 전환 대상으로 지원하지 않는다. 새 Guest
설정에는 VMI UID와 Worker UID가 필요하다. Guest instance와 session ID를 추가한 것은
TLS/mTLS나 외부 client 인증을 추가한 것이 아니다. 기존 namespace/network 접근 제한과
Controller token 관리 전제를 유지한다.

`Registered`, binding Ready, Session Active는 제어 상태다. 전체 CUDA API 호환성이나 quota
enforcement를 뜻하지 않는다. 세션 조회는 최대 128개씩 cursor pagination을 제공하며 페이지
사이 변경을 원자적으로 고정하지 않는다.

## 향후 전환 — 미실행

1. 누적 패치·의존성·C/Rust 컴파일과 6단계 시험을 검증할 수 있는 환경을 먼저 준비한다.
2. 아래 명령으로 명시적으로 이미지를 빌드하고 registry digest를 확정한다. 스크립트는 커밋된
   HEAD만 사용하며 image push를 실행하지 않는다.

```bash
./experiments/session-control-plane/build.sh cluster-manager REGISTRY/flyt/manager:stage7
./experiments/session-control-plane/build.sh worker REGISTRY/flyt/worker:stage7
./experiments/session-control-plane/build.sh guest-artifacts REGISTRY/flyt/guest:stage7
```

3. 새 `main-stage7` ControlPlane과 `gpu2-stage7` Profile을 준비한다. 기존 ControlPlane의
   image를 덮어쓰지 않는다. 별도 token Secret을 기존 3단계 절차로 생성한다. 예제 Profile은
   미승인이고 image/GPU UUID/node/UID는 placeholder다.
4. 생성된 Profile/ControlPlane과 실험 VM의 UID로 새 Request를 만든다. 대상 VM만 새 Request를
   선택한다. 기존 공유 Manager와 이를 사용하는 다른 VM은 유지한다.
5. CUDA client를 종료하고 대상 VM을 정상 stop/start해 새 VMI를 만든다. 기존 Worker 정리와
   새 Worker의 binding을 확인한다. Controller가 VM을 자동 재시작하거나 Profile을 승인하지 않는다.
6. 7단계 Guest Client Manager 및 필요한 동일 버전 guest 산출물을 명시적으로 설치하고,
   아래 도구로 새 VMI/Worker용 설정을 생성해 설치한다. 도구 자체는 SSH/설치/재시작하지 않는다.

```bash
python3 experiments/session-control-plane/guest-config.py \
  --context CONTEXT --namespace flyt-hami-stage3 --worker WORKER_NAME --output .local/guest-stage7
```

Guest OS 내부 reboot만으로 새 VMI가 만들어진다고 가정하지 않는다. 같은 VMI라도 Worker CR을
삭제/재생성하여 Worker UID가 달라졌다면 Guest 설정을 다시 생성해야 한다. Pod restart만으로
Worker UID가 유지된 경우에는 기존 세션을 복원하지 않으며 새 CUDA client 연결부터 처리한다.

Manager Pod 안에서 다음 명령으로 조회할 수 있도록 작성했다. 이전 조회 스크립트의 CSV/table
parser는 새 JSON 형식에 맞춰 별도로 전환해야 한다.

```bash
/opt/flyt/bin/flytctl list-bindings
/opt/flyt/bin/flytctl list-workers
/opt/flyt/bin/flytctl list-sessions
/opt/flyt/bin/flytctl list-sessions --after SESSION_ID
```

복구 시 해당 VM의 CUDA 작업을 종료하고 7단계 Worker/Request를 정상 해제한 뒤 이전 Request,
Guest Client Manager와 설정을 함께 되돌리고 새 VMI에서 시작한다. old/new Manager를 한
ControlPlane에서 섞거나 기존 CUDA 세션을 넘겨받는 복구는 제공하지 않는다.

## 검증 대기

| 항목 | 필요한 확인 |
|---|---|
| 빌드 | 기본→2→3→5→7 패치, Cargo feature·MongoDB 제외, C/Rust compile, legacy default build |
| Controller | 기존 observe/bind/list/unbind, 중복 reconcile, 지연 unbind 한계, epoch 변경 |
| 생성 경쟁 | 중복 open, 같은 GID, Guest daemon restart, 생성 도중 unbind, 응답 유실 |
| 정리 경쟁 | 이전 Closing 세션 정리 중 새 세션 생성, 옛 EOF/heartbeat/close, PID 재사용 |
| 장애 범위 | RPC 생성/종료 실패, Worker별 channel revoke, 다른 Worker 세션 유지 |
| Guest | deinit·SIGKILL·endpoint 전달 실패, /proc 확인, heartbeat 중단과 재시작 |
| 조회 | v7 schema, pagination, legacy 명령 거절, quota 0 표시 제거 |
| CUDA | 6단계 interception/memory/compute 재실행, 기존 PyTorch 19항목과 Graph 회귀 |
| 운영 | 새 ControlPlane/Guest 동시 전환, 이전 버전 복귀, 다른 GPU 설정 불변 |

6단계 probe/runner는 새 Guest와 Worker 환경을 준비한 뒤 재사용할 수 있도록 CUDA RPC를
보존했다. 진단 도구가 필요하면 6단계 진단 Containerfile에 7단계 Worker digest를 명시적으로
기반 이미지로 제공해야 한다. 이 조합도 미검증이다. 6단계 inventory는 STAGE7 metadata를
자동 수집하지 않으므로 추가로 `STAGE7.json`, patch checksum, 실제 Guest Client Manager와
Manager binary의 hash를 함께 보존해야 한다. 6단계 성공을 자동 승계하지 않는다.

현재 모든 항목은 NOT_RUN이다. 외부 플랫폼의 GPU 사용 해제나 실제 GPU 설정을 변경하지 않았다.
