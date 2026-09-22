# 2026-09-22 PyTorch 경로 개발 증거

판정과 한계는 [구현 보고서](../../PYTORCH_IMPLEMENTATION_2026-09-22.md),
재현 명령은 [재현 절차](../../REPRODUCE_PYTORCH.md)를 따른다.
모든 실행은 개발 검증이며 `formal_result=false`다.

- `shm-final2-a/b`: 같은 GPU에서 서로 다른 fixture로 동시 실행한 두 VM. 각각 네 조건,
  Channel Ready, 결과 회수와 Released 기록. `route.json`은 배포 정보를 필요한 필드만 추린 자료다.
- `shm-final-single2`: controller v2에서 같은 학습 binary로 재검증한 네 조건과 정상 회수.
  pair는 controller v1이므로 제어 버전의 반복 횟수를 합산하지 않는다.
- `passthrough-2026/2027-*`: 같은 GPU를 VFIO로 직접 할당한 VM의 여덟 조건.
- `passthrough-repeat-*`: seed 2026 네 조건의 별도 프로세스 재실행.
- `comparisons`: 조건당 18개 tensor의 오차·nonfinite·초과 원소 수와 원본 tensor SHA256.
- `trace`: native CUPTI 진단 요약. 계측기가 호출한 metadata API도 포함하므로 호출 수를
  성능 또는 필수 Guest API 수로 해석하지 않는다.
- `failures`, `cancel-recovery.json`: 실패를 보존하고, 수정 후 회복은 별도 결과로 연결한다.
- `source-sha256.json`, `artifact-sha256.json`: 미커밋 실행 소스·실제 binary/fixture/wheel 해시.
- `control-v2-source.json`, `controller-deployment.json`: 시작 전 취소 수정 버전과 실제 배포.

원본 tensor, 전체 로그와 배포 snapshot은 저장소 로컬 `.local/pytorch-path-20260922`에 있다.
이 디렉터리는 명시적으로 선택한 결과만 게시하며 private key·TLS/Secret·cloud-init userdata를
포함하지 않는다. GitHub에 tensor 원본이나 OCI image를 저장하지 않았다.

metrics의 시간·처리량은 correctness 실행의 진단 값이다. CPU 배치와 5회 독립 반복,
성능 calibration을 충족하지 않아 정식 E3/E4 성능 결과로 사용할 수 없다.
