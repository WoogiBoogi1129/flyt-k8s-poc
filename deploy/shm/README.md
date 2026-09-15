# SHM-only deployment sources — NOT_APPLIED

별도 실험 클러스터에서 Profile/Request CRD와 channel-crds.json을 함께 설치한다.
기존 RPC와 동일 group/version이므로 CRD 공존 migration 없이 기존 클러스터를 덮어쓰지 않는다.
ControlPlane/legacy FlytWorker는 사용하지 않는다. Channel Controller가 직접 Worker Pod를 소유한다.

먼저 Halted VM, 승인 Profile, 그 VM UID를 참조한 Request, 동일 node local filesystem PVC를
준비한다. Channel spec에는 vmRef/requestRef/pvcRef{name,uid}, sessions(1..32), uid/gid,
image(control), workerImage, hookImage(모두 digest), drain(false)를 지정한다.
Profile의 workerImage와 Channel workerImage는 일치해야 한다. PVC의 권한과 QEMU UID/GID를 맞춘다.
BackingReady 뒤 VM을 수동 시작하고 Guest에 해당 layout.bin을 안전하게 복사한다.

상태 쓰기 권한은 controller와 생성된 Worker serviceaccount에만 준다. admission은 Worker가
Guest/QEMU Detached를 주장하지 못하게 제한한다. operator는 CRD spec만 관리한다.
원격/force-delete 후 증거가 없으면 finalizer가 남는다. 수동 fencing 없는 강제 해제는 제공하지 않는다.
