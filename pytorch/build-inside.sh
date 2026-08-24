#!/usr/bin/env bash

set -Eeuo pipefail

pytorch_commit=70d99e998b4955e0049d13a98d77ae1b14db1f45
pytorch_repo=https://github.com/pytorch/pytorch.git
source_dir=/work/src/pytorch
wheel_dir=/work/wheels

install -d /work/src "$wheel_dir" /work/ccache
export CCACHE_DIR=/work/ccache
export PATH=/work/venv/bin:/usr/local/cuda/bin:$PATH
export CMAKE_C_COMPILER_LAUNCHER=ccache
export CMAKE_CXX_COMPILER_LAUNCHER=ccache
ccache --max-size=20G

if [[ ! -x /work/venv/bin/python ]]; then
  python3 -m venv /work/venv
  python -m pip install --no-cache-dir --upgrade \
    pip 'setuptools<82,>=70.1.0' wheel
fi

if [[ ! -d "$source_dir/.git" ]]; then
  git clone --filter=blob:none --no-checkout "$pytorch_repo" "$source_dir"
fi
git -C "$source_dir" fetch --depth=1 origin "$pytorch_commit"
git -C "$source_dir" checkout --detach "$pytorch_commit"
git -C "$source_dir" submodule sync --recursive
git -C "$source_dir" submodule update --init --recursive --depth=1 --jobs=8

cd "$source_dir"
rm -rf -- dist
python3 -m pip install --no-cache-dir -r requirements.txt

# Confirm that no CUDA object embeds libcudart_static. The three variables are
# intentionally redundant because PyTorch passes CUDA flags through several
# CMake layers.
export CUDA_NVCC_FLAGS='-cudart=shared'
export EXTRA_NVCCFLAGS='-cudart=shared'
export NVCC_APPEND_FLAGS='-cudart=shared'
export CAFFE2_STATIC_LINK_CUDA=0

python3 setup.py bdist_wheel
cp -a dist/torch-*.whl "$wheel_dir/"
git rev-parse HEAD > "$wheel_dir/PYTORCH_COMMIT"
python3 --version > "$wheel_dir/BUILD_TOOLCHAIN"
cmake --version | head -n 1 >> "$wheel_dir/BUILD_TOOLCHAIN"
ninja --version >> "$wheel_dir/BUILD_TOOLCHAIN"
nvcc --version >> "$wheel_dir/BUILD_TOOLCHAIN"
ccache --show-stats > "$wheel_dir/CCACHE_STATS"
(
  cd "$wheel_dir"
  sha256sum torch-*.whl > SHA256SUMS
)

torch_cuda_so="$(find build -type f -name libtorch_cuda.so -print -quit)"
[[ -n "$torch_cuda_so" ]]
readelf -d "$torch_cuda_so" | grep 'Shared library: \[libcudart.so.12\]' \
  > "$wheel_dir/DYNAMIC_CUDART_NEEDED"
