# Architecture

```text
KubeVirt VM A ─┐
               ├─ Flyt client manager ─ TCP 12402 ─┐
KubeVirt VM B ─┘                                    │
                                                    ▼
                                           Flyt Cluster Manager
                                           ├─ MongoDB resource policy
                                           └─ TCP 12401
                                                    │
                                                    ▼
                                whole-GPU Cell Pod on GPU node
                                      ├─ NVIDIA DRA ResourceClaim
                                      ├─ Flyt node manager
                                      ├─ Cricket RPC server per client
                                      └─ CUDA MPS
```

VM에는 NVIDIA PCI 장치나 `/dev/nvidia*`를 직접 연결하지 않는다. CUDA 호출은
shared cudart와 Flyt preload library를 통해 GPU Cell의 RPC server로 전달된다.
Kubernetes는 whole GPU의 예약과 GPU Cell 배치를 담당하고, Flyt는 VM별 SM 및
논리 GPU 메모리 정책을 담당한다.

이 경계 때문에 Kubernetes resource request와 Flyt 내부 quota는 서로 다른
계층이다. 두 값을 일치시키고 claim, Pod, Flyt server 상태를 함께 감시해야 한다.
