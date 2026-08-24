# Publishing to GitHub

로컬 저장소는 `main` branch로 초기화되어 있다. 공개 전에 Git 작성자와 저장소
소유자, 공개 범위를 확인한다.

```bash
git config user.name "YOUR_NAME"
git config user.email "YOUR_GITHUB_NOREPLY_EMAIL"
git commit -m "Initial reproducible Flyt Kubernetes PoC"
```

GitHub에서 빈 `flyt-k8s-poc` 저장소를 만든 뒤 원격을 연결한다. GitHub에서
README, `.gitignore`, license를 추가로 생성하지 않는다.

SSH:

```bash
git remote add origin git@github.com:OWNER/flyt-k8s-poc.git
git push -u origin main
```

HTTPS:

```bash
git remote add origin https://github.com/OWNER/flyt-k8s-poc.git
git push -u origin main
```

첫 push 뒤 GitHub Actions의 `static-validation`이 manifest rendering과 patch
reconstruction을 통과하는지 확인한다. release에는 대용량 binary 대신
`versions.lock.yaml`, checksum, SBOM과 redacted result summary를 연결한다.
