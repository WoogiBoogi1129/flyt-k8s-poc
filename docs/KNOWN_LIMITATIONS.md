# Known limitations

- 검증 범위는 단일 Blackwell GPU의 `1g.24gb` MIG와 VM 두 대다.
- cuDNN 9 backend descriptor/attribute 직렬화가 완전하지 않아 convolution
  workload가 실패할 수 있다.
- 실행 중 memory quota 변경은 대상 client 이탈 없이 안정적으로 동작한다고
  입증되지 않았다.
- multi-GPU와 NCCL은 범위 밖이다.
- Builder의 base image는 digest로 고정했지만 Ubuntu package mirror와 Python
  package index의 장기 bit-for-bit 보존까지 제공하지 않는다.
- GitHub-hosted runner에는 GPU/DRA 환경이 없으므로 GPU 시험은 self-hosted
  runner 또는 수동 재현이 필요하다.
- preflight의 `nvidia-smi`는 명령을 실행하는 호스트가 대상 GPU 노드라고 가정한다.
- 공개 기본 manifest는 MIG profile이다. archived report의 46→92 SM 동적
  재할당은 whole-GPU 실험 결과이며 기본 설정에서 `RUN_DYNAMIC=false`로 막는다.

따라서 이 결과를 범용 PyTorch 호환 또는 production-grade tenant isolation의
증거로 해석해서는 안 된다.
