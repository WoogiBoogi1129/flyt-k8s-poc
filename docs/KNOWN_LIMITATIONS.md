# Known limitations

- 최종 Track A2 검증 범위는 단일 Blackwell whole GPU, CUDA MPS, VM 두 대다.
  MIG 및 상용 NVIDIA vGPU는 최종 경로에 사용하지 않았다.
- cuDNN 9 backend descriptor/attribute 직렬화는 완전하지 않다. 검증 프로파일은
  `TORCH_CUDNN_V8_API_DISABLED=1`로 typed legacy convolution/BatchNorm 경로를
  사용하며 이 설정에서는 PyTorch matrix가 통과했다.
- 실행 중 memory quota 변경은 대상 client 이탈 없이 안정적으로 동작한다고
  입증되지 않았다.
- multi-GPU와 NCCL은 범위 밖이다.
- Builder의 base image는 digest로 고정했지만 Ubuntu package mirror와 Python
  package index의 장기 bit-for-bit 보존까지 제공하지 않는다.
- GitHub-hosted runner에는 GPU/DRA 환경이 없으므로 GPU 시험은 self-hosted
  runner 또는 수동 재현이 필요하다.
- preflight의 `nvidia-smi`는 명령을 실행하는 호스트가 대상 GPU 노드라고 가정한다.
- 공개 기본 manifest는 MIG profile이지만 Track A2 최종 실험은
  `21-gpu-cell-whole-pvc.yaml`의 whole-GPU 경로다. 46→92 SM 동적 재할당은
  기본 설정에서 `RUN_DYNAMIC=false`로 막는다.
- 두 VM의 표준 bounded CUDA smoke는 통과하지만, `compute_probe`의 iteration을
  `200000000`으로 비정상적으로 확대한 동시 스트레스 시 두 guest process가
  SIGSEGV로 종료된 과거 결과가 있다. client reaper와 zero-client deadlock은
  수정됐고 표준 15초 동시 부하는 두 VM 모두 성공 후 회수됐지만, 비정상적으로
  큰 커널이나 장시간 부하를 production 안정성의 증거로 사용하지 않는다.

따라서 이 결과를 범용 PyTorch 호환 또는 production-grade tenant isolation의
증거로 해석해서는 안 된다.
## cuDNN backend API

The VM compatibility profile defaults `TORCH_CUDNN_V8_API_DISABLED=1` because
Flyt's typed legacy cuDNN path is validated for convolution and BatchNorm
training, while the variable-size cuDNN backend descriptor protocol remains
incomplete. Applications can override the value explicitly for backend API
development and diagnostics.
