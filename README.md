# Flyt on Kubernetes/KubeVirt reproducible PoC

개발 중인 [1단계 HAMi 단독 PoC](experiments/hami-standalone/README.md)는
기존 MPS 구현과 분리되어 있으며 현재 **구현 완료·검증 미실행** 상태다.
이 브랜치의 [2단계 VM별 HAMi Worker](experiments/per-vm-worker/README.md)도
**구현 완료·검증 미실행** 상태이며, 별도 이미지와 정적 VM/Worker 배포 도구를 제공한다.
브랜치와 기준 버전 보존 방식은 [단계별 개발 문서](docs/DEVELOPMENT_STAGES.md)를 참고한다.

이 저장소는 Flyt를 Kubernetes와 KubeVirt 위에서 실행하고, NVIDIA DRA가
할당한 whole GPU를 GPU Cell이 소비하도록 만든 실험의 공개·재현 가능한 버전이다.
Flyt 원본을 그대로 배포하는 저장소가 아니며, 고정된 Flyt 기준 커밋에
`patches/series`의 패치를 적용한다.

## 무엇이 포함되는가

- Flyt 기준 소스와 PyTorch 소스를 full commit SHA로 고정
- K8s 설정 경로, GPU discovery/accounting, CUDA/PyTorch 호환 패치
- Builder, MongoDB, Cluster Manager, whole-GPU Cell, KubeVirt VM 매니페스트
- CUDA 및 PyTorch 호환성 시험, 그리고 과거 whole-GPU 동적 quota 시험 코드
- 비파괴 preflight와 범위가 제한된 cleanup
- 환경값을 분리하는 manifest renderer

아키텍처와 원본 대비 변경은 [아키텍처](docs/ARCHITECTURE.md),
[계보](docs/PROVENANCE.md), [패치 목록](docs/PATCH_CATALOG.md)을 참고한다.

## 요구 환경

- Kubernetes 1.34 계열과 `resource.k8s.io/v1` DRA API
- KubeVirt와 `virtctl`
- NVIDIA GPU DRA driver 및 `gpu.nvidia.com` DeviceClass
- CUDA 12.8 호환 NVIDIA GPU와 드라이버
- `kubectl`, `jq`, `rg`, `git`, `openssl`, `nvidia-smi`
- GPU 노드에서 실행할 수 있는 셸; preflight가 로컬 `nvidia-smi`를 사용한다

검증된 하드웨어는 Blackwell CC 12.0 whole GPU다. 다른 GPU 아키텍처는
CUDA arch와 기대 SM 값을 함께 수정하고 별도로 검증해야 한다.

## 빠른 시작

```bash
cp config.example.env config.env
# config.env의 REPLACE_* 값을 실제 클러스터 값으로 수정

make render
make validate
make preflight
make build
make control-plane
make gpu-cell
make vms
make start-vms
./scripts/seed-vm-resources.sh
```

Builder가 끝난 뒤 guest bundle과 PyTorch wheel을 설치하고 시험한다.

```bash
export PYTORCH_WHEEL="$PWD/artifacts/torch-2.11.0+flyt.cu128-cp310-cp310-linux_x86_64.whl"
./scripts/install-guests.sh
make test
./scripts/run-pytorch-matrix.sh
make evidence
```

실행 중 quota 변경 시험은 기본적으로 `RUN_DYNAMIC=false`다. 명시적으로 활성화한
whole-GPU 실험에서만 수행한다.

상세 절차와 판정 기준은 [REPRODUCING.md](docs/REPRODUCING.md)에 있다.
공유 클러스터에서 whole GPU 한 개와 VM 한 대부터 검증하는 실행 경로는
[WHOLE_GPU_EXPERIMENT.md](docs/WHOLE_GPU_EXPERIMENT.md)에 정리했다.
새 GitHub 저장소에 게시하는 절차는
[GITHUB_PUBLISHING.md](docs/GITHUB_PUBLISHING.md)에 정리했다.

## 안전성

`make preflight`는 다른 namespace의 GPU claim과 프로세스를 읽기만 하며,
GPU mode를 변경하거나 타 workload를 삭제하지 않는다. GPU gate가 실패하면
GPU Cell을 배포하지 않는다. `make cleanup`은 VM을 중지하고 GPU Cell만 제거한다.

## Artifact 정책

multi-GB guest bundle, PyTorch wheel, raw evidence는 Git에 넣지 않는다. 저장소에는
빌드 방법, 고정 입력, checksum과 축약 결과만 둔다. 자세한 내용은
[ARTIFACT_POLICY.md](docs/ARTIFACT_POLICY.md)를 참고한다.

## 현재 한계

이 저장소는 연구용 PoC다. cuDNN 9 backend와 실행 중 memory 변경 안정성은
완전하지 않다. 일반 목적의 다중 tenant GPU 운영 환경으로 간주하지 않는다.
[KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md)에 검증 경계를 기록한다.

## License

PoC 자동화와 문서는 MIT License로 배포한다. Flyt 패치는 원본 Flyt의 MIT
License와 저작권 고지를 유지한다. NVIDIA CUDA, cuDNN, PyTorch와 컨테이너
이미지는 각각의 라이선스가 적용된다.
