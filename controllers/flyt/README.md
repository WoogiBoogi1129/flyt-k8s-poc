# Flyt stage-3 Controller

`flyt.dev/v1alpha1`의 `FlytGPUProfile`, `FlytControlPlane`, `FlytWorker`와
KubeVirt `VirtualMachineInstance`를 각각 reconcile하는 namespaced Controller다.
소스 구현 상태이며 의존성 해결·빌드·시험·배포는 **NOT_RUN**이다.

- `api/v1alpha1`: API 타입과 수동 작성 DeepCopy.
- `cmd`: namespace cache, watch 연결, leader election과 health endpoint.
- `internal/controller/profile.go`: GPU 승인·기존 인프라 전제 관찰.
- `internal/controller/controlplane.go`: 공유 Manager 리소스와 잔여 binding 정리.
- `internal/controller/vmi.go`: VMI opt-in과 UID별 Worker 생성·삭제.
- `internal/controller/worker.go`: Worker 상태 전이와 동적 등록.
- `internal/controller/lifecycle.go`: 등록 해제와 finalizer 정리 순서.
- `internal/controller/resources.go`: 소유권이 명시된 Kubernetes 리소스.
- `internal/controller/binding.go`: 내부 Manager API 클라이언트.
- `internal/controller/common.go`: UID 소유권 검사, 상태·finalizer 갱신, 리소스 복구.

설치 CRD와 RBAC는 [experiments/controller/deploy](../../experiments/controller/deploy)에 있다.
사용법, 권한 및 검증 경계는 [3단계 안내](../../experiments/controller/README.md)를 따른다.
현재 Go 직접 의존성만 지정되어 있고 `go.sum`은 생성하지 않았다. 향후 명시적 이미지
빌드가 생성한 `go.mod`/`go.sum`을 추출하여 검증 기록과 함께 보관해야 한다.
