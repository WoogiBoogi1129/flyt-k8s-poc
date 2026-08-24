# Reproducing the PoC

## 1. 환경 구성

`config.example.env`를 `config.env`로 복사하고 node, parent GPU UUID/minor,
storage class, SSH 공개키와 개인키 경로를 설정한다. `config.env`는 Git에서
제외된다.

```bash
cp config.example.env config.env
make render
make validate
```

`deploy/rendered`의 모든 매니페스트를 검토한다. 공개키 외의 private key나
credential이 렌더링되면 안 된다.

## 2. 안전 gate

GPU 노드에서 다음 명령을 실행한다.

```bash
make preflight
```

PASS 조건은 대상 노드·GPU가 존재하고, 요청 profile에 맞는 MIG mode이며, 다른
workload가 GPU를 점유하지 않는 것이다. 스크립트는 타 claim을 삭제하거나 MIG
mode를 바꾸지 않는다.

## 3. 빌드와 제어 평면

```bash
make build
kubectl -n flyt-system wait --for=condition=Ready pod/flyt-builder --timeout=30m
kubectl -n flyt-system wait --for=condition=Ready pod/flyt-pytorch-builder --timeout=12h
make control-plane
```

실제 namespace가 다르면 위 명령의 `flyt-system`을 `config.env` 값으로 바꾼다.
두 Builder는 PVC cache를 사용한다. 완전한 clean build 측정이 필요하면 승인된
실험 namespace에서 PVC를 새로 만든다.

## 4. GPU Cell과 VM

```bash
make gpu-cell
make vms
make start-vms
./scripts/seed-vm-resources.sh
```

GPU Cell이 Ready이기 전에 VM workload를 시작하지 않는다. 각 VMI IP는 실행 시
조회하며 문서나 manifest에 고정하지 않는다.

## 5. guest 설치와 시험

PyTorch Builder의 wheel을 `artifacts/`로 복사하고 `PYTORCH_WHEEL`을 지정한다.
guest bundle은 `install-guests.sh`가 Flyt Builder에서 복사하고 checksum을
검증한다.

```bash
export PYTORCH_WHEEL="$PWD/artifacts/torch-2.11.0+flyt.cu128-cp310-cp310-linux_x86_64.whl"
./scripts/install-guests.sh
make test
./scripts/audit-api-surface.sh
./scripts/run-pytorch-matrix.sh
```

## 6. 증적과 정리

```bash
make evidence
make cleanup
```

`evidence/`와 `results/`는 기본적으로 Git에서 제외된다. 공개할 결과는 사용자명,
내부 IP, GPU UUID, credential을 제거한 summary만 `results/reference/`에 둔다.

## 판정 경계

- CUDA smoke: device 1개, 기대 MIG SM, kernel checksum
- 정적 memory quota: 지정 경계 다음 allocation이 OOM
- live SM: workload 연속성과 변경 전후 표본 수 충족
- PyTorch: subsystem별 PASS/FAIL을 분리
- cleanup: VM Halted, GPU Cell/claim 제거, 타 namespace 변화 없음

기존 실험의 알려진 실패도 동일하게 재현될 수 있다. 실패를 숨기지 않고
`KNOWN_LIMITATIONS.md`와 결과 summary에 기록한다.
