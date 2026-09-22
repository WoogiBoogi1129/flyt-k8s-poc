# SHM/HAMi 실증 도구

선행 개발과 본 실험을 분리한 실증 계획의 실행·판정 도구다. ivshmem launcher를
빌드·배포해 단일 VM과 2개 VM의 실제 SHM GPU 복사·PTX 실행을 확인했다.
PyTorch 학습 및 baseline 진입 조건은 미완료이므로 E1~E5 본 실험 전체를 완료한 상태는 아니다. 개발 중 수치는
`development/`에 보관하고 본 실험 성능으로 재사용하지 않는다.
실제 후속 구현·실패·조치는 [구현·검증 보고서](IMPLEMENTATION_AND_VALIDATION_2026-09-22.md)에 기록한다.

## 구성과 실행 범위

| 도구 | 구현된 기능 |
|---|---|
| `evidence.py` | 실노드 inventory, QEMU 장치 확인, 소스 해시, 294개 사례 행렬, 차단 사유·보고서, 예비 시간에서 공통 step 수 계산 |
| `train.py` | CPU 생성 fixture, 동일 FP32 eager MLP·SGD, 1/100 step tensor, 고정 step·전송 포함·window·별도 latency 모드 |
| `compare.py` | loss·gradient·갱신 parameter 전수 비교, NaN/Inf·누락·입력 불일치 거절 |
| `pair.py` | 두 guest 명령의 READY/GO/STARTED 장벽, host 시각 경계, timeout·명시적 원격 취소 |
| `analyze.py` | 반복·버전 일치 검사, 단일 VM 성능비, VM별 slowdown, 완료 chunk 기반 공통 구간 처리량 |
| `sample_cpu.py` | 서로 겹치지 않는 cgroup-v2 CPU 계측, cgroup 교체·소실 감지 |
| `memory_probe.cu` | 직접 메모리 할당·해제·재할당, 두 프로세스의 동시 합산 초과 요청 |
| `hami_smoke.py` | 새 namespace의 HAMi-only 검증, UUID·기존 소유권 확인, 실제 연산·OOM, 생성 자원 정리 |
| `guest_gpu_smoke.c` | 실제 VM의 SHM 복사·PTX 반복 결과 검사; PyTorch 대체 시험이 아님 |
| `../../scripts/run-evidence-smoke.py` | 새 VM 부팅·SSH·BAR 탐색·실제 probe 실행·증거 저장·drain·Released 확인 |
| `../../scripts/repeat-evidence-smoke.py` | 두 독립 PVC에서 새 allocation으로 준비·연산·회수·재사용 반복 |

자동 VM 프로비저닝, ivshmem QEMU 배포, GPU smoke와 회수 실행 도구를 추가했다.
**VFIO baseline, PyTorch CUDA 호환성, 학습 장애 시험과 본 실험 전체 자동 실행은 미완료다.**
필요한 선행 개발과 진입 증거는 `DEVELOPMENT_GATES.md`에 정리했다.
현재 행렬은 실행 순서와 증거 장부이며 배포 오케스트레이터가 아니다. `gates.json`을
PASS로 편집하는 것만으로 검증을 완료할 수 없다. 해당 빌드·환경에서 실제 증거가 필요하다.

기본 설정은 `config.json`이다. GPU 0의 회수 보류 allocation과 분리하기 위해
신규 예비 검증은 GPU 1 (`GPU-7d708c42-8d4a-16d5-0746-474567157aa3`)을 사용했다.
사용 가능 여부는 실행마다 재확인한다. GPU 번호보다 UUID를 식별자로 사용한다.

## 준비 및 사전 점검

저장소 루트에서 실행한다. coordinator는 Python 표준 라이브러리만 필요하다.
guest 학습 도구에는 같은 버전의 CUDA 지원 PyTorch가 필요하다.
테스트용 CPU PyTorch 환경을 GPU 실험 baseline으로 사용하지 않는다.

```sh
python3 experiments/evidence/evidence.py preflight --out .local/evidence-NEW
python3 experiments/evidence/evidence.py report --out .local/evidence-NEW
python3 -m unittest discover -s experiments/evidence/tests -v
```

`preflight`는 클러스터를 변경하지 않는다. 출력 디렉터리가 이미 존재하면 거절한다.
Pod env, cloud-init userdata, Secret, SSH/TLS 키는 수집하지 않는다. manifest의
source snapshot은 미커밋 소스도 포함하지만 인증 파일·`.local` 전체를 수집하지 않는다.
본 실험 전에 새 preflight를 수행해 최종 소스 상태를 고정한다.

## 학습 및 비교

CPU에서 만든 fixture를 **그대로 복사**해 각 guest에 배포한다. 같은 seed로
guest마다 다시 생성하지 않는다. `config.json`, 아래 Python 모듈도 동일 파일로 배포한다.

```sh
python3 experiments/evidence/train.py fixture --seed 2026 --out fixture-2026.pt
python3 experiments/evidence/train.py fixture --seed 2027 --out fixture-2027.pt
python3 experiments/evidence/train.py run --fixture fixture-2026.pt \
  --backend passthrough --mode correctness --batch 32 --input-mode resident --out reference
python3 experiments/evidence/train.py run --fixture fixture-2026.pt \
  --backend shm-hami --mode correctness --batch 32 --input-mode resident --out candidate
python3 experiments/evidence/compare.py reference/tensors.pt candidate/tensors.pt --out comparison.json
```

`--backend`는 결과의 이름이며 실행 경로를 설정하거나 입증하지 않는다. SHM guest는
별도로 검증된 preload·layout·BDF·slot을 설정해야 한다. Passthrough는 실제 PCI
할당이 필요하다. 경로·image digest·GPU UUID는 별도의 배포 증거와 연결한다.
`--device cpu`는 도구 검사 전용이며 결과에 `cpu_validation_only=true`를 기록한다.
모든 workload 실행은 `formal_result=false`로 기록된다. 정확성·경로·환경·전체 CPU
증거를 검토하기 전에는 실행 성공만으로 본 실험 PASS로 승격하지 않는다.

정확성은 warm-up 없이 1/100 step을 저장한다. 성능 모드는 최소 50 step의
warm-up 후 가중치·gradient를 초기화한다. 입력/target CPU 텐서는 미리 생성하며,
`transfer`는 매 step 두 텐서의 HtoD를 포함한다. TF32를 끄고 결정적 알고리즘을
요구한다. 런타임이 미지원이면 실패하며 CPU로 fallback하지 않는다.

예비 실행 결과를 다음 형식의 배열로 저장한다. 세 방식 × 두 batch × 두 입력 모드의
12개 조건이 모두 필요하다. `elapsed_seconds`는 warm-up 제외 실측값이다.

```json
[{"backend":"passthrough","batch":32,"input_mode":"resident","status":"PASS","steps":100,"elapsed_seconds":1.5}]
```

```sh
python3 experiments/evidence/evidence.py calibrate --pilots pilots.json --out calibration.json
```

부분 입력, 실패, 중복, 잘못된 시간은 거절한다. 가장 빠른 방식에서 30초 이상이 되도록
공통 step 수를 산출한다. 가장 느린 방식의 예상 warm-up+학습 시간을 기준으로 timeout을
산출한다. 예비 값은 절차 확인용이며 독립 반복을 대체하지 않는다. 큰 batch를 축소하면
설정과 fixture를 새 버전으로 만들고 모든 방식에서 다시 예비 실행한다.

## 두 VM 측정과 CPU

`pair.py` 입력은 `clients` 두 개이며 각각 `argv`, `cancel_argv` 문자열 배열을 가진다.
`argv`는 해당 guest에서 `train.py run --barrier ...`를 실행하고 stdin/stdout을 연결하는
명령이다. 원격 명령은 timeout 시 **원격 guest 프로세스까지** 종료할 수 있는
`cancel_argv`를 제공해야 한다. `start_timeout=300`, `training_timeout`은 calibration의 값이다.
fixture 전달·원격 결과 회수·CPU fixture 구성은 선행 개발 후 연결한다.

서로 다른 VM의 monotonic timestamp를 직접 비교하지 않는다. coordinator의 GO 전송과
STARTED 수신으로 각 VM 시작 시각의 범위를 구한다. `analyze.common_window`는
보장된 중첩 구간의 중앙 60초를 선택하고, 완전히 포함된 완료 chunk만 센다.
따라서 결과는 경계 chunk를 제외한 **보수적 처리량**이며 clock 불확실성과 함께 보고한다.
기본 window 모드는 10 step마다 동기화한다. 이 계측 비용을 E3 고정 step 측정과 혼합하지 않는다.

`sample_cpu.py --cgroup PATH --cgroup PATH --seconds N --out cpu.jsonl`로 측정한다.
QEMU/VM cgroup과 Worker/MPS/RPC cgroup은 서로 겹치지 않아야 한다. guest 프로세스 CPU는
진단 값이며 QEMU를 포함한 host 합계에 다시 더하지 않는다. `cpu.stat usage_usec`의
측정 전후 차이/1e6가 CPU-seconds이고 이를 측정 시간으로 나누면 평균 코어 수다.

## 자원 검증

CUDA 도구 체인에서 `nvcc --cudart shared memory_probe.cu -o memory-probe`로 빌드한다.
정적 cudart로 guest interceptor를 우회하지 않도록 공유 라이브러리를 사용한다.

```sh
./memory-probe below 268435456
./memory-probe free_reallocate 268435456
./memory-probe over 1610612736
./memory-probe aggregate_race 4294967296
```

숫자는 BYTES다. 첫 세 예제는 1024 MiB 한도의 예비 확인용이다. `boundary BYTES`는
명세에서 확정한 내부 예약/계상 규칙에 따른 경계 값을 입력해야 한다. CUDA 조회 free를
무조건 정답으로 삼지 않는다. `aggregate_race`는 부모가 CUDA 초기화를 하기 전에
fork하고, 두 자식의 초기화 이후 동시에 각각 quota의 60%를 요청한다. 두 호출이 모두
끝날 때까지 성공 메모리를 유지한다. SHM에서는 같은 VM의 서로 다른 slot 0/1을 쓴다.
해당 Channel에 2개 세션이 준비돼 있어야 한다. 둘 다 OOM이면 단독 적합성/예약량을
분리할 수 없으므로 `BLOCKED`(exit 3)이며, 둘 다 성공하면 `FAIL`이다.

연산 제한의 합격식은 아직 확정하지 않았다. 설치 libvgpu와 일치하는 소스·계측 구간을
확보하기 전에는 25/50/100 설정 전달이나 처리량 비만으로 PASS를 기록하지 않는다.

## HAMi-only 예비 실행

`docs/installation/hami-smoke.cu`를 현 CUDA 도구 체인으로 빌드한 바이너리를 전달한다.
소스·바이너리 해시와 실제 image digest를 결과에 저장한다.

```sh
python3 experiments/evidence/hami_smoke.py --binary /absolute/path/hami-smoke \
  --out .local/evidence-NEW/development/hami-smoke
```

전용 namespace만 생성·사용하며 고유 이름의 Pod·ConfigMap을 만든다. 다른 자원은
수정하지 않는다. 생성 UID를 확인해 종료 시 해당 자원만 정리한다. 예비 결과는
VM 합산 quota·연산 제한·SHM 학습 정확성의 증거로 사용하지 않는다.

## 결과 해석과 아직 남은 작업

E1은 양 seed·양 batch·양 입력 모드·3개 방식을 포함해 24개 사례다. E3는 계획대로
60회다. E4는 correctness/fixed/window/latency와 50 설정의 단독 기준까지 120개 사례다.
E2 43개, E5 필수 45개, 조건부 2개를 합쳐 전체 행렬은 294개다.

`analyze.py`의 정규화 입력은 backend, batch, input_mode, repetition, steps,
elapsed_seconds, status, correctness_pass, device, experiment_version, config_sha256,
source_sha256, gpu_uuid를 가진 배열이다. `correctness_pass`는 해시로 연결한 실제
comparison 결과를 검토한 후에만 설정한다. 분석기는 입력 provenance의 진실성을
자동 입증하지 않는다. CPU 비용은 cgroup 증거와 별도 결합한다.

최종 측정 전 snapshot/이미지/설정을 동결하고 5개 교차 블록을 수행한다. 미완료 배포
어댑터, E2 명세, E5 장애 fixture 때문에 현재 단계에서 전체 실증이 완료됐다고
표현해서는 안 된다. 실제 실행·검증 결과는 `EXECUTION_2026-09-22.md`를 참고한다.

측정 구현 참고: [PyTorch 재현성](https://docs.pytorch.org/docs/stable/notes/randomness.html),
[CUDA 비동기 측정](https://docs.pytorch.org/docs/stable/notes/cuda.html).
