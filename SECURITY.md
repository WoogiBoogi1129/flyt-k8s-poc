# Security policy

이 저장소는 연구용 GPU virtualization PoC다. 공개 issue에 credential, kubeconfig,
SSH key, raw cluster inventory 또는 tenant data를 첨부하지 않는다.

공개 전 다음을 확인한다.

```bash
make validate
git grep -nE 'BEGIN .*PRIVATE KEY|password:|token:'
find . -type f -size +20M -not -path './.git/*'
```

GPU Cell은 host driver와 CUDA MPS를 공유하므로 VM 수준의 물리 GPU isolation을
제공하지 않는다. production tenant에 적용하기 전에 별도의 위협 모델과 보안
검토가 필요하다.
