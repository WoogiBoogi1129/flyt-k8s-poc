# 공개 개발 검증 자료

이 자료는 VM의 SHM GPU 복사·PTX·메모리·회수 개발 검증이다.
PyTorch 학습 정확성이나 Passthrough/RPC·MPS 성능 비교의 결과가 아니다.

- [최종 판정 및 실패·조치 보고서](../../IMPLEMENTATION_AND_VALIDATION_2026-09-22.md)
- [실행 단위 색인](development/index.json): 공개 파일의 SHA256과 당시 실행 판정
- [최초 버전 생명주기 20회](plots/lifecycle-summary.json)
- [최종 버전 생명주기 20회](plots-final/lifecycle-summary.json)
- [본 실험 차단·미실행 상태](main-experiment-status.json)
- [실제 배포 이미지·환경](deployment-summary.json)
- [PyTorch wheel의 정적 CUDA API 감사](pytorch-shm-abi.json)
- [회귀 검사 로그](checks/)
- [최종 잔여 자원 점검](final-state-summary.json)
- [GPU 관측 시계열](gpu-observations.csv) 및 [관측 구간·원시 해시](gpu-observations-summary.json)

`development`는 allowlist로 추출한 metrics, timeline, probe 출력, 이미지·binding 식별자와
프로그램 해시를 포함한다. SSH/TLS 키, Secret, Pod 전체 환경, cloud-init, 전체 원시 디렉터리는
포함하지 않는다. `manifest.json`의 `git_commit`은 당시 HEAD이며 미커밋 구현이 있었다.
최종 실행 소스 해시는 별도 `final-source-hashes.json` 및 이미지 digest와 함께 해석한다.

실행 실패와 실험 판정은 다르다. 의도적으로 종료한 피해 VM의 일반 smoke는 FAIL이어도
상위 장애 실험은 기대한 종료·동료 연산·회수를 확인하면 PASS일 수 있다.
반대로 최초 `fault-worker-pod/1..5`의 PASS는 잔여 자원 감사에서 **FAIL로 정정**됐다.
당시 원본 metrics를 덮어쓰지 않으므로 최종 보고서와 `verdict-adjustments.json`을 우선한다.
`run-image-recovery`에는 수동 이미지 수정이 포함되며 자동 회수 성공 횟수에서 제외한다.

최초 controller/Worker v2와 최종 controller v2b/Worker v3b의 반복·시간은 합산하지 않는다.
전체 PASS/FAIL 파일 수를 그대로 실험 성공률로 계산하지 않는다.
