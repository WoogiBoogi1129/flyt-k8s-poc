# SHM/HAMi 구현·실험 및 실제 화면 증거 계획

수정일: 2026-09-24. 사용자 지시에 따라 **학습 정확성 비교 및 장애 격리·복구 실험을 이번 범위에서 제외**한다.
이 문서는 이번 자료 제작 범위를 정의하며, 2026-09-22 보고서·실패 기록·기존 전체 실험 gate를 소급 변경하지 않는다.

## 변경·유지 범위

| 구분 | 내용 |
|---|---|
| 변경 | 실행 경로 연결 그림 대신 실제 명령 출력을 조회하는 브라우저 화면과 원본 snapshot을 저장 |
| 삭제 | loss/gradient/parameter 비교, 정확성 오차 화면 및 신규 tensor 비교 실행 |
| 삭제 | guest/Worker 강제 종료·삭제, 제어기 재시작, 장애 격리·복구 반복과 장애 영상 |
| 보강 | 메모리 할당 유지 구간, 실제 GPU process 메모리, HAMi monitor 원본 지표 및 캡처 |
| 유지 | 두 VM 독립 Channel/Worker 및 동일 물리 GPU 동시 실행 |
| 유지 | 메모리 제한·OOM 후 정상 재할당, 일반 종료·회수·신규 allocation 재사용 |
| 유지 | 연산 제한 계약 확정 후 25/50/100 실측, 동일 조건의 세 방식 성능 비교 |
| 유지 | digest/소스/바이너리 해시, 반복 실행, 실패 기록, 원본과 캡처 연결 |

OOM 요청은 메모리 제한 실험에 속한다. 프로세스/Pod 강제 종료 등 장애 주입과 구분한다.
GPU smoke의 계산 결과 검사는 실제 CUDA 실행의 성공 확인이며, 제외된 학습 tensor 정확성 비교가 아니다.

## 실행 항목

| ID | 항목 | 실행·증거 | 종료 조건 |
|---|---|---|---|
| S1 | 실제 SHM 경로·두 VM 공유 | 서로 다른 PVC/Channel/Worker, 같은 UUID, guest BAR, 45초 PTX 실행, live browser capture | 두 Channel Ready, 독립 식별자, 실제 GPU 프로세스 중첩, 각 실행 성공 |
| S2 | 메모리 제한·복구 | quota 1024/4096 MiB 각각 baseline→할당 15초 유지→초과 요청→해제 15초→재할당 15초; 별도 경계 suite | 실제 메모리 증가·감소, 예상 OOM, 재할당 및 계상 복구 |
| S3 | 두 세션 합산 제한 | 4096 MiB quota, slot 0/1의 동시 60% 요청 | 하나 성공/하나 OOM 및 정상 종료·회수. 둘 다 OOM은 BLOCKED |
| S4 | 정상 회수·재사용 | 동일 버전 20개 새 VM/allocation, 두 PVC 번갈아 사용 | 20회 Released, 실행 Pod 잔류 없음, allocation/generation 중복 없음, backing 비어 있음 |
| S5 | 연산 제한 | 설치 limiter의 소스·빌드·관측 계약을 확보한 후 25/50/100, 60초×3회 | 설정 전달과 제한 성립을 분리하고 사전에 고정한 허용오차로 판정 |
| S6 | 세 방식 성능 | passthrough / RPC·MPS / SHM·HAMi, batch 32/1024, resident/transfer | 같은 workload·GPU·CPU/NUMA 예산, warm-up≥50, 측정≥30초, 독립 5회 반복 |

S5/S6의 선행 조건이 없으면 미실행/차단으로 명시한다. 설정 값이나 개발 smoke 시간을 성능 우위 증거로 대체하지 않는다.
두 VM 공유 성능은 보장된 공통 60초 구간과 각 VM의 단독 대비 slowdown을 함께 보고한다.
CPU fallback 부재·작업 완료 등 성능 실행의 유효성 검사는 유지하되 학습 tensor 비교는 새로 실행하지 않는다.
실제 노드 단절·다중 GPU는 이번 범위에 포함하지 않는다.

## 실제 화면과 관측 명령

`serve-evidence-dashboard.py`는 localhost에서 고정된 읽기 전용 명령과 HAMi monitor를 조회한다.
브라우저는 그 결과를 주기적으로 표시하며 `capture-evidence-dashboard.cjs`가 실제 Chromium 화면을 PNG로 저장한다.
화면의 관측 sequence와 함께 같은 JSON을 보존하며, 캡처 전후 sequence가 바뀌면 다시 시도한다.
이는 실제 조회 화면의 캡처이고 정적 그림이나 생성 이미지가 아니다. 기존 Grafana가 설치됐다는 의미도 아니다.

```sh
kubectl get vmi,flytsharedmemorychannels,pods -n flyt-evidence
kubectl get pod WORKER -n flyt-evidence -o json
# GPU 노드 또는 NVIDIA 도구가 있는 관측용 Pod에서 실행
nvidia-smi -i GPU_UUID --query-gpu=timestamp,uuid,memory.used,memory.total,utilization.gpu --format=csv
nvidia-smi -i GPU_UUID --query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory --format=csv
curl http://192.168.24.21:31992/metrics
```

Pod 배정값, HAMi의 컨테이너 계상값, 물리 장치/프로세스 사용값을 구분한다.
HAMi 수집 갱신 지연이나 context 예약량으로 값이 다를 수 있으므로 동일 값으로 강제 정규화하지 않는다.
물리 GPU 전체 사용률은 VM별 제한의 통과 근거가 아니다. 각 snapshot의 시작·완료 시각을 보존하며
여러 명령을 동시에 실행한 원자적 snapshot이라고 주장하지 않는다.

## 재현과 산출물

1. 실노드·기존 소유권·이미지 digest·PVC Released 여부를 확인하고 새로운 실행 디렉터리를 만든다.
2. 고정 Guest shim과 Worker를 사용하고 변경한 memory probe를 다시 빌드한다.
3. live collector와 Chromium capture를 시작하고 S1~S4를 수행한다.
4. 정상 회수·backing·GPU 유휴 및 기존 자원 보존을 감사한다.
5. 명시적 allowlist로 결과를 내보내고 실측 그래프·PNG·source hash를 저장한다.
6. GitHub에는 코드, 범위 변경, 실제 결과, 캡처 및 원본 증거를 반영한다. SSH/TLS 키, cloud-init, 전체 `.local`, OCI는 게시하지 않는다.

최종 보고서에는 실제 수행한 항목과 미완료 항목을 분리한다. 장애·정확성 비교 영상은 제작하지 않는다.

## 실행 도구 사용 예

GPU 호스트에서 실행한다. 이미지 import·PVC 준비는 기존 재현 절차를 따른다. `images.json`에는
`control`, `worker`, `hook`의 digest 고정 참조를 넣고, artifacts에는 `guest-key(.pub)`,
`libflyt_guest.so`, `guest-gpu-smoke`, `memory-probe-guest`, 선택적으로 `compute-probe-guest`를 둔다.
공개 저장소에 키를 넣지 않는다. probe는 CUDA 개발 컨테이너에서 현재 shim에 동적 연결해 빌드한다.

```sh
# 별도 터미널 1: 실행 전부터 수집. snapshots 디렉터리는 새 경로여야 한다.
python3 scripts/serve-evidence-dashboard.py \
  --runs .local/showcase-NEW/runs --output .local/showcase-NEW-snapshots \
  --prefix evidence-new-

# 별도 터미널 2: Playwright 설치 위치를 NODE_PATH로 제공한다.
# npm install --prefix .local/browser-tools playwright
# .local/browser-tools/node_modules/.bin/playwright install chromium
NODE_PATH="$PWD/.local/browser-tools/node_modules" \
  node scripts/capture-evidence-dashboard.cjs .local/showcase-NEW-screenshots

# 별도 터미널 3: output은 새 디렉터리. 25/50/100 탐색 관측은 선택 옵션이다.
python3 scripts/run-evidence-showcase.py --output .local/showcase-NEW \
  --images LOCAL_IMAGES_JSON --artifacts LOCAL_ARTIFACT_DIRECTORY \
  --prefix evidence-new --lifecycle-count 20 --include-compute

# collector를 종료하기 전 최종 화면을 저장한다.
touch .local/showcase-NEW-screenshots/STOP
python3 experiments/evidence/plot_showcase.py \
  --snapshots .local/showcase-NEW-snapshots --runs .local/showcase-NEW/runs \
  --output .local/showcase-NEW-plots
```

`plot_showcase.py`에는 matplotlib이 필요하다. 이미지 보존 Pod를 사용했다면 모든 실험의 정상
회수와 최종 감사를 마친 뒤 해당 UID의 Pod만 삭제한다. 로컬 OCI import는 pull 가능한 registry의 대체 운영 방안이 아니다.

compute probe의 PASS는 CUDA 부하 실행·종료 성공만 의미한다. 10초 warm-up 후 약 61초 부하를
관측하며 처리량/장치 사용률을 저장한다. 허용오차와 계측 유효성이 확정되지 않은 상태에서는
`formal_enforcement_verdict=NOT_EVALUATED`를 유지한다. 정식 S5 및 세 방식 S6 성능 비교와 구분한다.

실제 두 VM 동시 실행·회수 화면을 짧게 녹화할 때는 다음 도구를 별도 실행한다.
`VM_PREFIX`에 해당하는 두 VM이 Ready가 되면 Chromium 녹화를 시작하고 Released 또는 120초에 종료한다.

```sh
NODE_PATH="$PWD/.local/browser-tools/node_modules" \
  node scripts/record-evidence-dashboard.cjs .local/showcase-NEW-video VM_PREFIX
```

이는 실제 브라우저 녹화이며, PNG를 이어 붙인 영상이나 연출된 장애 영상이 아니다.
