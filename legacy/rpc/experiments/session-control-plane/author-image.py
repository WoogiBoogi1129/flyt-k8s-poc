#!/usr/bin/env python3
"""Author a separate stage-7 Containerfile from the preserved stage-5 definition."""
from pathlib import Path
root = Path(__file__).resolve().parent
text = (root.parent / "hami-backend/Containerfile").read_text()
text = text.replace("COPY experiments/hami-backend /input/stage5", "COPY experiments/hami-backend /input/stage5\nCOPY experiments/session-control-plane /input/stage7")
text = text.replace("    && make -C submodules patchelf/install", '''    && while IFS= read -r item; do \\
         [ -z "$item" ] || [ "${item#\\#}" != "$item" ] || \\
           patch -p1 --forward < "/input/stage7/patches/$item" || exit 1; \\
       done < /input/stage7/patches/series \\
    && make -C submodules patchelf/install''')
text = text.replace("    && LOG=INFO make install-cmgr", '''    && cargo build --manifest-path control-managers/Cargo.toml --release --no-default-features --features hami-control-plane \\
         --bin flyt-session-manager --bin flyt-session-client-manager --bin flyt-sessionctl --bin flyt-node-manager \\
    && install -m 0755 control-managers/target/release/flyt-session-manager bin/flyt-cluster-manager \\
    && install -m 0755 control-managers/target/release/flyt-session-client-manager bin/flyt-client-manager \\
    && install -m 0755 control-managers/target/release/flyt-sessionctl bin/flytctl \\
    && install -m 0755 control-managers/target/release/flyt-node-manager bin/flyt-node-manager''')
text = text.replace("bin/flyt-cluster-manager bin/flytctl bin/flytctlnet", "bin/flyt-cluster-manager bin/flytctl")
text = text.replace("    && cp /input/stage5/versions.json /out/STAGE5.json", '''    && cp /input/stage5/versions.json /out/STAGE5.json \\
    && sha256sum /input/stage7/patches/*.patch > /out/STAGE7_PATCHES.sha256 \\
    && cp /input/stage7/versions.json /out/STAGE7.json''')
text = text.replace("COPY --from=build /out/STAGE5_PATCHES.sha256 /out/STAGE5.json /opt/flyt/metadata/",
    "COPY --from=build /out/STAGE5_PATCHES.sha256 /out/STAGE5.json /out/STAGE7_PATCHES.sha256 /out/STAGE7.json /opt/flyt/metadata/")
(root / "Containerfile").write_text(text)
