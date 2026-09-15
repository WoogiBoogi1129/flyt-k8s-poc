# 10-04 VM SHM adapter — source authored, NOT_RUN

`runtime/shm`에 독점 allocation별 PVC backing/layout 생성, Host 파일 mapping,
Guest의 명시적 PCI BDF BAR2 mapping, KubeVirt domain XML hook을 작성했다.
ivshmem-plain + polling-v1을 사용한다. GPU PCI enable/bind/reset과 doorbell은 구현하지 않는다.
실제 설치·파일 생성·VM 연결·compile·test는 실행하지 않았다.

backing은 로컬 파일시스템 PVC로 제공하고 QEMU/Worker를 같은 노드에 배치한다.
NFS/원격 파일시스템을 CPU cache-coherent 공유 RAM으로 취급하지 않는다.
UID/GID는 설치 환경에서 지정한다. Guest의 resource2 권한·cache 속성, SELinux/AppArmor,
QEMU 추가 인자 허용, Sidecar feature 및 PVC sharedComputePath 지원 확인은 검증 대기다.
사용 중인 KubeVirt 버전이 확인되지 않아 범용 배포 성공을 주장하지 않는다.

Hook은 `/flyt-channel` PVC를 읽고 QEMU는 동일 PVC의 `/var/run/flyt-channel` 경로를 사용한다.
Sidecar annotation에는 pvc.name, volumePath, sharedComputePath를 함께 지정해야 한다.
hook 실행만으로 PVC가 QEMU에 공유되었다고 보지 않는다. 제공자가 양쪽 mapping ACK 후 실행을 연다.
layout.bin은 64-byte LE header와 세션당 80-byte LE record인 control artifact이며 SHM wire ABI와 별개다.
새 allocation 생성 실패 시 기존 파일을 덮어쓰지 않는다. detach 확인 후 회수는 10-05/09 책임이다.

참고: [QEMU ivshmem 사양](https://www.qemu.org/docs/master/specs/ivshmem-spec.html)은 BAR2와
PCI ID를 정의한다. [KubeVirt PVC hook 예제](https://kubevirt.io/user-guide/debug_virt_stack/launch-qemu-strace/)
의 sharedComputePath 구성을 사용한다. [Hook 문서](https://kubevirt.io/user-guide/user_workloads/hook-sidecar/)
상 v1.9부터 deprecated이므로 초기 adapter는 Sidecar v1alpha2를 지원하는 설치에 한정한다.
신규 Plugin API 지원은 별도 adapter 작업이며 자동으로 대체하지 않는다.
