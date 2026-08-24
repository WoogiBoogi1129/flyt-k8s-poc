# Artifact and evidence policy

## Git에 포함

- source patch와 patch series
- build/deploy/test script
- image digest와 source commit lock
- 작은 redacted 결과 summary와 checksum
- 재현 문서와 알려진 한계

## Git에 포함하지 않음

- guest bundle과 PyTorch wheel
- container image layer
- raw Kubernetes/MongoDB 로그
- kubeconfig, SSH private key, Secret YAML
- 내부 IP, 실제 GPU UUID가 포함된 전체 cluster inventory

사전 빌드 산출물을 배포할 때는 OCI registry 또는 release storage를 사용하고
SHA256, 생성 commit, image digest, build flags와 SBOM을 함께 게시한다. 소스에서
동일 산출물을 다시 만들 수 있는 경로가 항상 기본 경로다.
