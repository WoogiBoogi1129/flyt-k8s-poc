# Source provenance

## 재현 기준점

| 계층 | 기준 |
|---|---|
| Flyt baseline | `WoogiBoogi1129/flyt_custom_for_k8s` |
| Flyt commit | `596a86939bae125d127a9ed6f70d92c332f47644` |
| PoC changes | `patches/series`에 기록된 순서 있는 patch set |
| PyTorch | `v2.11.0`, commit `70d99e998b4955e0049d13a98d77ae1b14db1f45` |
| CUDA image | `versions.lock.yaml`의 digest 고정 이미지 |

Flyt baseline 저장소 자체가 이미 K8s를 지칭하는 포크다. 따라서 변경점을
설명할 때 다음 두 층을 구분한다.

1. Flyt/Cricket 계열의 기존 분산 GPU 가상화 구조와 baseline 포크의 역사
2. baseline commit 위에 이 PoC가 추가한 K8s, MIG, 최신 CUDA/PyTorch 변경

이 저장소가 직접 보증하는 것은 두 번째 층이다. baseline 이전 변경을 이번
PoC의 개발 성과로 귀속하지 않는다.

## 소스 재구성

다음 명령은 기준 commit을 별도 cache에 받고 patch series를 적용한다.

```bash
cp config.example.env config.env
make fetch-source
git -C .cache/flyt-source diff --stat
```

Builder Pod도 같은 repository, commit, patch 순서를 사용한다. 기준 SHA와 image
digest는 `versions.lock.yaml`이, 환경별 값은 Git에서 제외되는 `config.env`가
담당한다.

## 라이선스 계보

Flyt baseline은 MIT License이며 Cricket 기반 CUDA API virtualization layer를
포함한다고 명시한다. patch를 재배포할 때 원본 저작권과 MIT License를 보존한다.
PyTorch, CUDA, cuDNN 및 컨테이너 이미지는 각 프로젝트의 라이선스를 따른다.
