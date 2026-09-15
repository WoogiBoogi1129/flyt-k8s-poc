# 단계별 개발과 기준 버전 보존

## 기준 버전

- 기존 소스 기준: `f6552296f6e355c70ddcd8133857703b3ae622ee`.
- `main`: 기준 버전을 유지하고 미검증 단계를 자동 병합하지 않는다.
- `baseline/flyt-mps`: 기준 소스 tree를 그대로 보존하는 브랜치.
  동결을 기록하는 빈 커밋에는 `[skip ci]`를 사용한다. 부모가 기준 커밋이며 파일 내용은 동일하다.
- 기존 문서의 PyTorch 19/19 결과는 과거 보고된 MPS 실험 결과다.
  이 작업에서 해당 결과를 재실행하거나 현재 클러스터에서 재확인하지 않았다.

Git branch는 추적 파일만 보존한다. 외부 Flyt baseline SHA와 patch series는
기존 `versions.lock.yaml`/`patches/series`를 통해 추적한다. 이미지 digest, 빌드 산출물,
ignored config, 원본 로그는 별도 보관해야 하며 브랜치 생성만으로 실행 환경 전체가
백업됐다고 표현하지 않는다. 개인 설정/키를 baseline에 추가하지 않는다.

## 단계 상태

| 단계 | 브랜치 | 구현 | 검증 |
|---|---|---|---|
| 0: MPS 기준 동결 | `baseline/flyt-mps` | 소스 보존 | 과거 결과만 존재; 재검증 안 함 |
| 1: HAMi 단독 PoC | `stage/01-hami-standalone` | 구현 완료 | NOT_RUN |
| 2: VM별 정적 HAMi Worker | `stage/02-per-vm-worker` | 구현 완료 | NOT_RUN |
| 3: CRD 기반 VM/Worker Controller | `stage/03-controller` | 소스 구현 완료 | NOT_RUN |
| 4: VM GPU 요청과 quota 변환 | `stage/04-gpu-request` | 소스 구현 완료 | NOT_RUN |
| 5: HAMi 자원 제어 backend 분리 | `stage/05-hami-backend` | 소스 구현 완료 | NOT_RUN |
| 6: FLYT+HAMi End-to-End 실험 도구 | `stage/06-hami-e2e` | 도구 소스 구현 완료 | NOT_RUN; 실제 검증 단계 미완료 |
| 7: Manager 자원 관리 역할 축소 | `stage/07-session-control-plane` | 소스 구현 완료 | NOT_RUN |
| 8·9: 호환성·Multi-VM 검증 | 별도 개발 없음 | 사용자 요청으로 보류 | NOT_RUN |
| 10-01: SHM 전환 계약 | `stage/10-01-shm-contract` | 계약·인터페이스 작성 완료 | NOT_RUN; runtime 미연결 |
| 10-02: CUDA 실행 모듈 | `stage/10-02-cuda-dispatch` | 기본 Runtime dispatcher/backend 소스 작성 완료 | NOT_RUN; runtime 미연결 |
| 10-03: SHM Queue | `stage/10-03-shm-queue` | ring·payload·직렬화·polling 소스 작성 완료 | NOT_RUN; VM/runtime 미연결 |

첨부 계획의 요약표와 본문은 후반 단계 번호가 서로 다르므로, 후속 작업은 번호와
기능명을 함께 기록한다. 1단계의 범위는 독립 HAMi quota 실험으로 명확히 제한한다.

## 작업 규칙

1. 각 단계는 별도 브랜치에서 개발하고 완료 시 해당 브랜치를 GitHub에 push한다.
2. 구현 상태와 검증 상태를 별도로 기록한다. 미실행 시험을 PASS로 기록하지 않는다.
3. 이후 단계는 선택한 이전 단계의 정확한 commit에서 분기한다. 이전 브랜치를
   덮어쓰거나 force-push하지 않는다. 필요하면 검증 대기 의존성을 명시한다.
4. 1단계는 요청에 따라 빌드·테스트·렌더 검증·GPU 실행을 생략한다.
   push로 기존 CI가 시작되지 않도록 커밋 메시지에 `[skip ci]`를 사용한다.
   공용 workflow를 삭제/비활성화하지 않는다. 검증을 재개하는 커밋에서는 skip을 제거한다.
5. 실제 GPU/플랫폼 설정 변경은 단계 코드 작성이나 Git push와 별개의 작업이다.
   GPU 소유권/환경이 준비되기 전까지 설치·시험 명령을 실행하지 않는다.

## 1단계 구현

[HAMi standalone 안내](../experiments/hami-standalone/README.md)에 설치 설정,
CUDA probe, 실행/수집/정리 절차 및 미검증 항목을 정리했다.
기존 MPS 관리, CUDA RPC, KubeVirt VM, patch series는 변경하지 않는다.

## 2단계 구현

`stage/01-hami-standalone`의 `260e3cd1bb2f1683926e892bdbb41485e2dec38e`에서 분기했다.
[VM별 Worker 안내](../experiments/per-vm-worker/README.md)에 구현, 배포 전제,
guest 연결, 개별 삭제와 검증 대기 항목을 정리했다.

VM별 Worker Deployment와 정적 CM 매핑을 추가하고 Worker 안의 프로세스별 RPC 구조는
유지한다. 별도 image/patch series에서 HAMi 경로를 추가하며, 실행에 필요한 최소한의
MPS 제어 우회를 5단계에서 앞당긴다. VMI watcher/controller와 SHM은 포함하지 않는다.
기존 1단계 구현, 기본 이미지와 patch series는 그대로 보존한다.

2단계도 검증 없이 개발하는 조건을 유지하여 패치 적용·빌드·정적/렌더 검사·GPU 실행·
클러스터 배포를 수행하지 않는다. 커밋에 `[skip ci]`를 사용한다. 1단계 quota 검증이
완료됐다는 전제는 충족하지 않았으며 2단계 구현 완료와 별개의 검증 대기 의존성이다.

## 3단계 구현

`stage/02-per-vm-worker`의 `6423897213057bf3ec0411c213f880ad549dda5f`에서 분기했다.
[Controller 안내](../experiments/controller/README.md)에 CRD 책임, 생성·삭제 순서,
장애 복구 범위, 설치 준비와 검증 대기 항목을 정리했다.

`FlytGPUProfile`은 승인된 GPU UUID와 정적 quota, `FlytControlPlane`은 공유 Manager,
`FlytWorker`는 VMI UID별 Worker와 binding을 관리한다. 4개의 reconcile loop를
Go/controller-runtime으로 작성했다. VM 추가·삭제 때 공유 Manager를 재시작하지 않도록
별도 stage-3 패치에서 동적 등록 API를 추가했다. 실제 GPU 할당은 기존 HAMi가 담당한다.

CRD 단위 관리를 위해 4단계에서 계획한 요구량 표현 중 최소 정적 Profile만 앞당겼다.
사용자별 ResourceQuota 정책, live quota 변경, SHM, API descriptor는 포함하지 않는다.
동일 VMI 이름 재사용과 Worker 재시작은 UID/실행 세대로 구분하며 CUDA 세션의 투명 복원은
지원하지 않는다. 단계별 이미지가 달라 기존 Worker를 그대로 인수하지 않는다.

3단계도 패치 적용 검사, 의존성 해결, 빌드, 정적 검사, CRD/CEL·매니페스트 검사,
reconcile 시험, GPU 실행과 실제 배포를 수행하지 않았다. 검증 결과는 모두 NOT_RUN이며
커밋에 `[skip ci]`를 사용한다. 1·2단계 소스, 기본 patch series와 main은 보존한다.
GPU 소유권은 재확인하거나 변경하지 않았으며, 기존 외부 플랫폼의 사용 해제는 별도 전제다.

## 4단계 구현

`stage/03-controller`의 `cdf086e921e6ce14a46ccca50528c2115a1f1fb3`에서 분기했다.
[GPU 요청 안내](../experiments/gpu-request/README.md)에 CRD 책임과 변경 정책, 배포 순서,
권한 및 검증 대기 항목을 정리했다. 1·2·3단계 실험 디렉터리와 runtime 패치는 보존한다.

`FlytGPURequest`에 VM/Profile/ControlPlane UID 참조와 count/compute/memory를 선언한다.
Profile은 승인된 실행 환경과 Worker별 상한을 제공한다. 정규화된 값 하나에서 HAMi의
Pod requests/limits와 FLYT_MEMORY_BYTES를 계산하며, 다중 GPU와 live resize는 포함하지 않는다.

VMI Controller가 유일한 자동 Worker 생성자다. Request Controller는 검증·상태·삭제 조정을
담당한다. 최초 admission의 요청값을 VMI annotation과 Worker spec에 고정해 같은 VMI에서
Worker를 재생성해도 값을 바꾸지 않는다. 요청 변경은 PendingRestart로 기록하고 새 VMI에서
반영한다. Request 삭제 또는 승인 철회는 현재 할당의 명시적 해제다.

4단계 Controller는 기존 3단계 설치를 같은 namespace/Lease/리소스 식별자로 확장한다.
별도의 HAMi 설치나 GPU 설정 변경은 없다. 빌드·정적 검사·CRD 검사·시험·실제 배포는 모두
미실행이며 커밋에 `[skip ci]`를 사용한다. 개발 완료를 실행 가능성 검증으로 해석하지 않는다.

## 5단계 구현

`stage/04-gpu-request`의 `ed46a8939521ee5a59f38a39c6a15359d85fb34a`에서 분기했다.
[HAMi backend 안내](../experiments/hami-backend/README.md)에 실행 경로·차단 명령·이미지
전환과 검증 대기를 기록했다. 1~4단계 실험, Go Controller/CRD와 기본 패치는 보존한다.

기본→2단계→3단계 뒤에 별도 5단계 패치를 추가한다. C 자원 제어 dispatch에서 HAMi와 legacy
MPS 구현을 분리하고, 5단계 이미지는 C MPS 구현을 제외하며 Rust에도 HAMi 빌드 정책을 내장한다.
메모리 조회 오류를 CUDA 응답에 전달하고, CLI·Manager·Node Manager·RPC queue의 자원 변경과
checkpoint/migration 명령을 실행 전에 거절한다. HAMi에서 기존 SM/memory 장부의 비교·증감을
우회하며 실제 quota는 기존 HAMi에 맡긴다. RPC API와 handle/pointer mapping은 보존한다.

4단계의 불변 image/ref 정책에 따라 새 Profile/ControlPlane/Request로 명시적으로 전환한다.
VM 자동 재시작이나 GPU 설정 변경은 없다. patch 적용·빌드·정적 검사·GPU 실행·실제 배포는
모두 NOT_RUN이며 `[skip ci]`로 커밋한다. 6단계 interception·quota 검증과 CUDA 회귀 시험은 남아 있다.

## 6단계 구현

`stage/05-hami-backend`의 `63f93010479499003918f88f55917f55fd81067a`에서 분기했다.
[End-to-End 실험 도구 안내](../experiments/hami-e2e/README.md)에 대상 고정, GPU 실행 전제,
함수 진입 증거와 quota 측정·보고 절차를 기록했다. 1~5단계 소스와 기본 patch series,
Go Controller/CRD를 보존하며 새 runtime 패치는 없다.

실험기는 CRD의 VMI/Worker/Pod/Request UID, Worker generation, Manager epoch와 quota를
읽어 대상을 고정한다. CUDA Runtime/Driver/async 메모리 시험, 동일 Worker의 두 client 합산
경계, CPU 결과 비교, GDB 기반 HAMi allocation/launch 진입 추적과 두 quota 처리량 비교를
작성했다. 기존 전체 GPU Cell/Manager reset 스크립트는 호출하지 않는다.

진단 Worker는 5단계 이미지에 probe/GDB/binutils만 추가한다. 기존 Controller의 ptrace 권한은
확장하지 않으므로 정책상 추적이 불가능하면 BLOCKED다. 라이브러리 로드만으로 interception,
kernel 성공만으로 compute 제한을 PASS로 기록하지 않는다. 신규 Profile/Request로 전환하며
승인·VM 재시작·실험 실행은 명시적 후속 절차다.

이 단계도 빌드·정적 검사·패치 검사·도구 실행·매니페스트 검사·GPU 실행·배포를 수행하지
않고 `[skip ci]`로 커밋한다. 구현 완료는 검증 도구의 소스 작성 완료이며 원래 계획의
6단계 검증 성공을 의미하지 않는다. 기존 GPU/외부 플랫폼 설정은 변경하지 않는다.

## 7단계 구현

`stage/06-hami-e2e`의 `c960d13bf729085d6226311b7175b2a91da22798`에서 분기했다.
[세션 Control Plane 안내](../experiments/session-control-plane/README.md)에 코드 분리,
프로토콜 변경과 전환·복구 절차를 기록했다. 원문 본문의 7단계인 Manager 자원 관리 역할
축소이며, 요약표의 Multi-VM/Lifecycle 검증 완료를 의미하지 않는다.

별도 Cargo feature/실행 경로에서 MongoDB·GPU quota 장부·placement를 제외하고 binding,
Worker 등록과 세션을 관리한다. 세션 기본 키는 서버가 생성한 ID이며 VMI/Worker/Pod UID,
Worker generation, Guest instance와 GID로 연결을 식별한다. 지연 정리는 이전 세션과 RPC ID에
한정하며 통신 결과가 불명확하면 해당 Worker generation을 종료한다.

기존 Controller binding/Node Manager v3와 CUDA RPC는 유지한다. Guest 제어는 v7 JSON으로
변경되어 새 Guest Client Manager와 설정이 필요하며, 조회 CLI도 v7 JSON으로 구분한다.
신규 ControlPlane/Profile/Request로 전환하고 기존 Go Controller/CRD와 이전 단계는 보존한다.

패치 적용·의존성 해결·빌드·정적 검사·프로토콜 시험·GPU 실행·배포는 모두 NOT_RUN이다.
`[skip ci]`로 커밋하며 실제 GPU나 외부 플랫폼 설정은 변경하지 않는다.

## 10-01단계 구현

7단계 `c3d381f312b32012738d0c105517b789b5b72f5d`에서 분기한다. 사용자 요청에 따라 8·9단계
검증과 CPU 배포 실험을 보류하고 SHM 전용 data path를 목표로 개발한다. RPC 자동 fallback은 없다.

[SHM 전환 계약](../experiments/shm-contract/README.md)에 descriptor ABI, Queue/dispatcher
함수 선언, channel/session JSON Schema, VM 시작 전 allocation과 VMI 생성 후 binding,
동일 노드 배치·mapping ACK·회수 절차를 작성했다. 설치용 CRD·Queue 함수 본체·장치 adapter는
후속 10-03~05 범위다. 기존 runtime과 패치에는 아직 연결하지 않는다.

10-02~10-10은 별도 후속 브랜치로 진행한다. 헤더 compile·Schema 검증·정적 검사·build·test·
배포·GPU 실행은 모두 NOT_RUN이며 `[skip ci]`로 커밋한다. GPU/노드 설정은 변경하지 않는다.

## 10-02단계 구현

10-01 커밋 `1a35dce6e01539a4078429cb951f9ea517598301`에서 분기했다.
[CUDA 실행 모듈](../experiments/cuda-dispatch/README.md)에 typed dispatcher, 기본 Runtime
backend, 세션별 handle/범위 검사, 결과 버퍼 소유권, 오류 후 세션 종료 처리를 작성했다.
RPC/XDR stub 호출 없이 CUDA를 호출하는 별도 라이브러리 소스이며, 기존 RPC handler 전체를
교체하거나 Guest/Worker 실행 경로에 연결한 것은 아니다. SHM adapter는 10-06에 연결한다.

한 프로세스당 한 세션·한 실행 thread로 제한하며, CUDA cleanup 오류는 해당 프로세스 종료가
필요하다. 실제 supervisor 연결은 후속 작업이다. async/Graph/라이브러리 API도 후속 범위다.
CMake configure·빌드·링크·정적 검사·테스트·배포·GPU 실행은 모두 NOT_RUN이다.
이전 브랜치, 기존 패치, GPU/노드/외부 플랫폼 설정은 보존하고 `[skip ci]`로 커밋한다.

## 10-03단계 구현

10-02 커밋 `d47670f0effaa79f64254a74a1133adfaaec4a6e`에서 분기했다.
[SHM Queue](../experiments/shm-queue/README.md)에 전체 layout 검사, header/descriptor 직렬화,
SPSC ring, private payload snapshot, Guest submit/receive, Worker take/respond와 polling을 작성했다.
한 세션에 한 in-flight 요청을 허용하며 counter/identity 오류와 게시 후 timeout을 실패 처리한다.
RPC fallback과 자동 재실행은 없다. 이미 매핑된 메모리를 받는 라이브러리이며 VM mapping,
Controller gate, CUDA payload 해석과 실행 adapter는 아직 연결하지 않았다.

configure·compile·link·정적 검사·테스트·프로세스 간 실행·배포·VM/GPU 실행은 모두 NOT_RUN이다.
이전 단계와 실제 GPU/노드 설정을 보존하고 `[skip ci]`로 GitHub에 반영한다.

## 10-04 VM SHM adapter

`stage/10-04-vm-shm-channel`: [backing·mapping·hook](../experiments/vm-shm-channel/README.md) 소스 작성. 모든 검증 NOT_RUN.
