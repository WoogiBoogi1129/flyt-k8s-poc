# 본 실험 이전 개발 트랙

현재 전체 선행 개발은 미완료다. 아래 표는 구현 과제와 종료 조건이며, 실제 통과 범위는 후속 보고서와 연결한다.

2026-09-22 후속 개발에서는 D1의 실제 VM 부팅·BAR·SHM 왕복과 D2의 두 VM
독립 GPU 실행을 확인했다. D4는 수동 보조 없이 정상 회수·신규 allocation 재사용
20회를 통과했다. 최종 controller v2b / Worker v3b에서도 별도로 20회를 다시 통과했다.
메모리 초과 후 세션이 닫히던 오류도 수정해 두 quota에서 재시험을 통과했다.
Worker Pod 강제 삭제 때 남던 재생성 Pod를 수정하고 새 버전에서 5회 재검증했다.
세부 실행 및 최종 판정은 [구현·검증 보고서](IMPLEMENTATION_AND_VALIDATION_2026-09-22.md)를 따른다.
후속 PyTorch 구현에서는 D3 대표 학습과 D5의 passthrough 기능 부분을 검증했다.
두 VM의 8개 학습 조건은 실제 passthrough와 1/100 step 결과가 모두 일치했다.
RPC·MPS, CPU/NUMA 통제, 연산 제한 계약은 남아 있어 전체 본 실험 진입은 아직 차단된다.
최신 판정은 [PyTorch 구현·검증 보고서](PYTORCH_IMPLEMENTATION_2026-09-22.md)를 따른다.

| ID | 구현·준비 작업 | 종료 조건/증거 |
|---|---|---|
| D1 | 현재 KubeVirt 1.9 launcher와 같은 배포판·QEMU 계열에서 ivshmem을 활성화한 QEMU/launcher 빌드. downstream 패치·module 경로·libvirt 동작 보존 | image digest, `-device help`의 ivshmem-plain, 기본 VM 부팅, hook domain XML, guest BAR, SHM 왕복 |
| D2 | 2개 VM의 독립 Channel/PVC/session, 지원 CUDA 복사·PTX kernel 실행 배포. guest/Worker image와 GPU UUID 연결 | 양쪽 Mapped 및 Channel Ready, 서로 다른 입력의 실제 GPU 결과, native GPU 우회 없음 |
| D3 | MLP 실행 시 필요한 Runtime registration/launch·라이브러리 API를 추적하고 지원. `cudaLaunchKernel`·fatbinary·packed 인자와 필수 entry 구현(대표 경로 PASS) | 같은 PyTorch 빌드에서 forward/backward/SGD, 1/100 step 결과, 지원 API와 변경 목록 |
| D4 | 정상 종료 시 launcher terminal/Worker exit 증거를 관측·보존하도록 수명주기 개선 | 테스트용 finalizer 없이 일반 종료·Released·backing 삭제·신규 allocation 재사용. 이전 세대 재사용 금지 |
| D5 | GPU 1의 IOMMU/VFIO 전환·원복, passthrough VM과 보존 RPC/MPS baseline 구성 | 같은 UUID/guest/PyTorch/CPU·NUMA 조건, baseline 자체 반복 정확성, GPU 중복 소유 없음 |
| D6 | VM당 8 vCPU/16 GiB, Worker당 4 CPU 초기 예산에 대한 실제 배치 및 계측·회수 어댑터 | immutable image IDs, guest/host CPU 집합, 서로 겹치지 않는 cgroup 경로, guest 실행/취소/결과 회수 명령 |
| D7 | 설치 libvgpu v2.10.0 바이너리와 일치하는 소스·컴파일 설정을 확보하고 quota 계약 확정 | 메모리 내부 예약량·다중 프로세스 계상 범위·경계 값; 연산 limiter 대상/구간/허용오차와 관측 도구 |
| D8 | 격리 장애 fixture 및 제어기 복구 어댑터 구성 | VM A의 각 장애 주입·원상복구, VM B의 정확성/연속 실행, 회수 보류와 완료의 독립 판정 |

GPU 1은 2026-09-22 점검에서 NUMA 0, BDF `0000:41:00.0`, IOMMU 그룹 26에
단독 장치로 관측됐다. 후속 PyTorch 검증에서 HAMi 제외를 실제 probe로 확인한 후 VFIO 전환,
passthrough VM 학습, NVIDIA 원복과 HAMi 4개 GPU 재등록까지 수행했다.
다음 전환에서도 HAMi 등록/예약과 실제 GPU 사용을 새로 확인해야 한다.

이전 설치 보고서의 시험용 finalizer 보조 성공을 D4 통과로 승계하지 않는다.
기존 Draining 채널 a는 그대로 보존하고, 새 allocation으로 검증한다.
Pod 부재·Node NotReady·API 접근 실패는 detach 완료의 근거가 아니다.

전체 개발 종료 후 새 디렉터리에 preflight를 수행하고, 실제 증거 파일과 해시를
각 gate에 연결한다. correctness gate는 E1 결과로, calibration gate는 전체 방식의
예비 실행으로 채운다. 개발 버전의 성능값을 본 실험에 편입하지 않는다.

실제 노드 단절은 외부 관측·복구 및 유지보수 범위를 별도로 확보할 때 수행한다.
다중 GPU는 2개 물리 장치의 실행/매핑/부분 실패/회수가 구현된 뒤 별도로 수행한다.
