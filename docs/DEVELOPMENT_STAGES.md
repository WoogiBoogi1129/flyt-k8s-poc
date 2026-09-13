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
| 3: VM/Worker controller | `stage/03-controller` (예정) | 미착수 | NOT_RUN |

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
