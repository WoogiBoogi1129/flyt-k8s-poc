# SHM/HAMi 후속 구현·실증 작업 기록

작업일: 2026-09-22 KST. 대상: gpu-4, `flyt-evidence` namespace.
기준 커밋: `6bc0332`. **진행 중인 기록이며 전체 실증 성공 보고서가 아니다.**

## 구현과 조치

| 문제 | 확인한 원인 | 조치 | 현재 확인 범위 |
|---|---|---|---|
| QEMU ivshmem 미지원 | launcher의 CentOS QEMU 장치 구성에 ivshmem 없음 | 동일 10.1.0-20.el9 소스 RPM의 downstream 패치를 적용하고 CONFIG_IVSHMEM 활성화 | 최종 launcher에서 ivshmem-plain 확인 |
| 빌드 소스 경로 오류 | 재귀 검색이 하위 Brotli configure 선택 | 정확한 QEMU 소스 루트로 고정 | 빌드 통과 |
| configure 옵션 오류 | 해당 버전에 enable-vhost-vsock 옵션 없음 | 지원 옵션과 장치 설정 사용 | configure 통과 |
| Python 빌드 의존성 | tomli 미설치 | meson/tomli/pycotap 버전 고정 | QEMU 컴파일 통과 |
| BIOS 탐색 실패 | custom 설치 위치에서 QEMU 상대 경로가 달라짐 | 펌웨어 디렉터리 제공 및 -L 경로 명시 | QMP 초기화와 실제 VM 부팅 통과 |
| VGA와 PCI 슬롯 충돌 | custom argv의 ivshmem이 libvirt VGA보다 먼저 slot 1 점유 | XML의 PCI 주소를 검사해 빈 root slot 명시 | 실제 VM 부팅, BAR `0000:00:1e.0` 확인 |
| hook의 root controller 거절 | KubeVirt는 libvirt가 root controller를 채우기 전 XML로도 hook 호출 | 알려진 q35 machine의 암묵적 root 지원; 다른 미지원 구성은 거절 | 회귀 검사 및 실제 VM 통과 |
| 실험 runner의 PCI 탐색 구문 오류 | 원격 Python 문자열에 literal newline 삽입 | `chr(10)`으로 분리자 생성 | 후속 VM의 BAR 탐색 통과 |
| 로컬 이미지 pull 실패 | 지정 digest의 Worker/hook이 런타임 저장소에서 조회되지 않음 | 보존 OCI의 정확한 digest를 확인해 재import | Worker 실제 실행 확인 |
| 종료 증거 관측 경쟁 | launcher API 객체가 terminal 상태 수집 전에 삭제될 수 있음 | controller 소유 finalizer로 객체 보존, Detached 저장 후 해당 finalizer만 제거 | 실패한 첫 VM에서 Guest/Worker Detached→Released 및 reclaim 완료 |
| 불완전한 종료 판정 | 일부 container status만 존재해도 이전 조건 통과 가능 | 일반/init/ephemeral container 전체 이름과 종료 상태 일치 요구 | 단위 검사 통과 |
| Guest 메모리 조회 누락 | cudaMemGetInfo SHM 경로 없음 | 0x1013 opcode, Worker 실제 조회 및 16-byte LE 응답, Guest Runtime/Driver wrapper 추가 | 1 GiB·4 GiB VM의 quota·잔여량 조회 성공 |
| OOM 이후 정상 요청까지 실패 | 실행기가 cudaMalloc의 error 2도 치명적 오류로 취급해 session 종료 | 메모리 부족만 복구 가능한 거절로 처리; 다른 오류의 보수적 종료 유지 | 단위 검사와 실제 VM 2개 quota의 OOM 후 재할당·계상 복구 통과 |
| 합산 메모리 probe fork | Guest 라이브러리는 fork 상속 상태를 의도적으로 거절 | 각 자식이 exec 후 독립 slot으로 연결하도록 변경 | 빌드 완료, 실기 검증 진행 |

소스 RPM: [CentOS Stream 공식 Koji](https://kojihub.stream.centos.org/kojifiles/packages/qemu-kvm/10.1.0/20.el9/src/qemu-kvm-10.1.0-20.el9.src.rpm).
SHA256: `daf957b455f55bac4aa7268a5d57d279ae65aa579f89b0b6e758e7df9e9cafad`.
CentOS·launcher 기반 image와 Python 빌드 의존성은 Containerfile에서 고정한다.

## 변경된 동작

- `images/flyt/Qemu.Containerfile`: 해당 QEMU의 배포판 패치를 보존하는 ivshmem launcher 빌드.
- `domain_hook.py`: root-bus의 기존 PCI 주소와 chipset 예약 슬롯을 피하는 명시적 주소 할당.
- `observe.py`: 종료 증거를 먼저 저장하고 Pod 보존을 나중에 해제. API 실패/노드 단절/부분 상태에서는 회수 완료로 판정하지 않는다.
- active controller에만 Pod update 권한 추가. review 권한은 그대로 유지한다.
- `prepare-evidence-vm.py`: 새 UID로 VM/Profile/Request/Channel 생성. 이전 Channel이 Released일 때만 PVC 재사용.
- `import-evidence-image.py`: 임시 관리 Pod로 지정 OCI만 host CRI-O 저장소에 import. 서비스 재시작·GPU binding 변경 없음.
- `set-evidence-launcher.py`: KubeVirt customization으로 새 VMI launcher 선택. 기존 실행 VM의 이미지는 변경하지 않으며 복원 patch를 저장한다.

## 실제 실행 범위

첫 VM은 BIOS 경로 오류, 두 번째 VM은 PCI 슬롯 충돌로 부팅하지 못했다.
첫 VM의 drain은 추가 수동 finalizer 조작 없이 양쪽 Detached, reclaim 성공,
Channel Released까지 도달했다. 이 결과는 학습 성공이나 일반 학습 후 종료 20회 검증과 구분한다.
세 번째는 초기 domain XML 처리, 네 번째는 runner의 원격 Python 구문 오류로 실패했다.
네 번째 VM 자체는 정상 부팅했다. 다섯 번째 VM은 SHM 복사 4 KiB와 PTX kernel 100회
검사, Channel Ready, 정상 종료·Released를 통과했다.

두 VM을 장벽으로 동시에 시작한 추가 개발 시험에서 seed 2026/2027의 서로 다른
결과를 각각 2,325/2,344회 검사했다. 각 VM은 약 10초간 실제 GPU 연산을 수행했고,
두 Channel 모두 Released가 됐다. 이는 E4의 학습·공유 성능 결과가 아니다.

초기 메모리 시험은 1 GiB·4 GiB 모두 경계까지 할당하고 경계+1 byte를 거절했지만,
OOM 뒤 session이 닫혀 재할당·계상 복구에 실패했다. Worker v2로 다시 실행한 결과는 다음과 같다.

| 요청 quota | 최초 계상상 잔여량 | 경계 할당 | 경계+1 byte | OOM 후 재할당 | 최종 잔여량 |
|---|---:|---|---|---|---:|
| 1 GiB | 497,025,024 B | 성공 | CUDA error 2 | 성공 | 497,025,024 B |
| 4 GiB | 3,718,250,496 B | 성공 | CUDA error 2 | 성공 | 3,718,250,496 B |

각 경우 최초 내부 계상량은 576,716,800 B(550 MiB)였다. 이는 이 빌드의 관측치이며
모든 GPU·CUDA·HAMi 조합의 고정 예약량으로 일반화하지 않는다. 경계는 CUDA 조회가
반환한 잔여량으로 정의한 개발 진단이다. 본 E2에는 설치된 limiter의 소스·설정 계약도 필요하다.

같은 Worker v2를 사용한 정상 생명주기 20회 반복과 추가 격리 장애 검증은 진행 중이다.

대표 PyTorch 학습·passthrough/RPC·MPS 성능 비교는 아직 수행 결과가 없다.
PTX smoke·메모리 시험이 통과하더라도 PyTorch 학습 정확성의 대체 증거로 사용하지 않는다.

보존된 `torch-2.11.0+flyt.cu128` wheel을 정적으로 검사했다. `libc10_cuda.so`의 CUDA
import 44개 중 27개, `libtorch_cuda.so`의 209개 중 178개가 현재 Guest 라이브러리에
export되지 않는다. 이 숫자는 전체 라이브러리 검사이며 MLP가 모두 호출한다는 뜻이 아니다.
구체적으로 `__cudaRegisterFatBinary`/`__cudaRegisterFunction` 등록 경로가 없고,
현재 `cudaLaunchKernel`은 명시적으로 not-supported를 반환한다. 따라서 PyTorch
forward/backward/SGD를 검증한 상태가 아니다. 정적 감사 도구는
`scripts/audit-pytorch-shm-abi.py`이며 native CUDA 라이브러리로의 우회를 성공으로 세지 않는다.

## 증거 관리

원시 자료는 `.local/implementation-20260922/`에 저장한다. 빌드 로그(v1~v4),
import 로그, QMP 검사, VM 실패 로그, 각 VM/Channel 입력, 회수 상태와 image digest가 포함된다.
같은 디렉터리에 실험용 SSH/TLS 키도 있으므로 **디렉터리 전체를 공유하거나 Git에 추가하지 않는다.**
GitHub에는 코드와 이 보고서 및 재현 절차만 반영한다.
