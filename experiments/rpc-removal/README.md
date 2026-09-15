# 10-10 RPC runtime removal — 소스 작성, 검증 NOT_RUN

기본 Makefile/Containerfile/배포 renderer/향후 CI 대상을 SHM으로 전환했다.
새 build에는 외부 Cricket checkout, rpcgen/XDR, libtirpc, rpcbind, RPC Server/Manager/Node Manager,
MongoDB가 없다. CUDA 데이터 포트 Service/NetworkPolicy 및 RPC readiness를 기본 배포에서 제외했다.
HTTP admission Service만 생성하며 Kubernetes control API와 KubeVirt hook gRPC는 남긴다.
이 작업은 ONC RPC CUDA 데이터 경로 제거이며 모든 관리 RPC를 없애는 작업이 아니다.

기존 파일은 `legacy/rpc/`로 이동했고 이전 브랜치들은 수정하지 않았다. 새 이미지에는 legacy
디렉터리를 COPY하지 않는다. 과거 문서/스크립트의 상대경로는 원래 브랜치에서 재현해야 한다.
실행 중인 Kubernetes 리소스나 외부 GPU 플랫폼은 삭제·변경하지 않았다.

## 빌드와 배포 입력

- CUDA 12.8.1/cudnn 개발 image, CMake/C11 compiler; Worker는 shared CUDA/cuBLAS/cuDNN에 연결한다.
- 별도 hook image는 실제 설치 KubeVirt Sidecar-shim digest를 `KUBEVIRT_SHIM_IMAGE`로 전달한다.
  `/sidecar-shim` binary 경로와 v1alpha2 protocol 지원은 해당 image에서 검증해야 한다.
- 전용 local filesystem PV/PVC를 승인 GPU node에 고정한다. NFS/원격 filesystem은 거절한다.
- `scripts/render-shm.py --namespace flyt-shm-NAME --image IMAGE@sha256:DIGEST --ca CA.pem`은
  JSON manifest만 출력한다. admission TLS Secret의 SAN은 `flyt-shm-admission.NAMESPACE.svc`다.
  TLS Secret을 자동 생성하거나 kubeconfig/context를 선택해 apply하지 않는다.
- `deploy/shm`의 CRD에는 SHM 전용 Request schema가 있다. controlPlaneRef는 제거했다.
  같은 group/version의 기존 RPC CRD를 변경하므로 **별도 실험 클러스터용**이다. 기존 RPC 사용
  클러스터에 일괄 적용하지 않는다. 별도 CRD group/version으로 공존하는 migration은 추가 작업이다.
- Worker image는 Profile의 승인 image와 같아야 하며 Channel sessions는 maxClients 이하다.
- Guest layout.bin 배포와 BDF 선택은 명시적으로 수행한다. Guest GPU device 노출은 필요하지 않다.

## 최종 개발 상태

| 단계 | 소스 범위 | 검증 |
|---|---|---|
| 10-04 | 파일 backing, Host/Guest mapping, domain hook | NOT_RUN |
| 10-05 | Channel/Attachment CRD, reconcile, admission | NOT_RUN |
| 10-06 | 기본 CUDA Guest/Worker SHM 연결 | NOT_RUN |
| 10-07 | stream/event/staging 및 명시적 ABI PTX launch | NOT_RUN |
| 10-08 | Graph lifecycle/SGEMM/cuDNN lifecycle 부분 지원 | NOT_RUN; 전체 호환 미완료 |
| 10-09 | heartbeat, 세대 제한, detach 증거와 allocation 회수 | NOT_RUN |
| 10-10 | SHM 전용 기본 build/deploy, RPC archive | NOT_RUN |

**모든 계획을 기능적으로 완료했다고 볼 수는 없다.** 특히 아래 구현은 아직 남아 있다.

1. Runtime fatbinary/function registration, cubin/ELF 입력 및 자동 kernel argument ABI 지원.
2. Graph compute/memcpy nodes와 capture/update, CUDA async 동작·오류 의미의 전체 호환.
3. cuBLASLt/cuDNN 연산/cuSOLVER/cuSPARSE/cuFFT 및 기존 PyTorch API surface 이관.
4. KubeVirt 신규 Plugin adapter, 자동 Guest endpoint 배포, 기존 RPC cluster와 CRD 공존 migration.

이들은 테스트만 남은 항목이 아니라 추가 소스 구현이 필요한 항목이다. 이번 결과는 제한된 지원
집합의 SHM 전용 개발 경로다. 이전의 넓은 RPC 호환성을 자동으로 이어받지 않는다.
빌드·정적 검사·CRD/CEL·Queue·VM mapping·CUDA/HAMi·호환성·장애 검증을 모두 미실행했다.
user 요청의 8·9단계 검증 보류도 유지된다. 추가 CI 정의를 작성했으나 `[skip ci]`로 커밋하며
이번 작업에서 workflow/build/test를 실행하지 않았다.
