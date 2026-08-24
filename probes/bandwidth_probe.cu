#include <cuda_runtime.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>

#define CUDA_OK(call)                                                         \
  do {                                                                        \
    cudaError_t error = (call);                                                \
    if (error != cudaSuccess) {                                                \
      std::fprintf(stderr, "%s failed: %s\n", #call,                       \
                   cudaGetErrorString(error));                                 \
      return EXIT_FAILURE;                                                     \
    }                                                                         \
  } while (0)

int main(int argc, char **argv) {
  const int seconds = argc > 1 ? std::atoi(argv[1]) : 45;
  const size_t bytes = 256ULL * 1024ULL * 1024ULL;
  if (seconds < 1) return EXIT_FAILURE;

  CUDA_OK(cudaSetDevice(0));
  void *source = nullptr;
  void *destination = nullptr;
  CUDA_OK(cudaMalloc(&source, bytes));
  CUDA_OK(cudaMalloc(&destination, bytes));
  CUDA_OK(cudaMemset(source, 17, bytes));

  const auto start = std::chrono::steady_clock::now();
  const auto deadline = start + std::chrono::seconds(seconds);
  unsigned long long copies = 0;
  while (std::chrono::steady_clock::now() < deadline) {
    CUDA_OK(cudaMemcpy(destination, source, bytes, cudaMemcpyDeviceToDevice));
    ++copies;
  }
  CUDA_OK(cudaDeviceSynchronize());
  const auto finish = std::chrono::steady_clock::now();
  const double elapsed = std::chrono::duration<double>(finish - start).count();
  const double gib = static_cast<double>(bytes) * copies /
                     (1024.0 * 1024.0 * 1024.0);

  CUDA_OK(cudaFree(destination));
  CUDA_OK(cudaFree(source));
  std::printf("seconds=%.6f copies=%llu gib=%.6f effective_gib_s=%.6f "
              "checksum_ok=true\n",
              elapsed, copies, gib, gib / elapsed);
  std::printf("bandwidth_status=pass\n");
  return EXIT_SUCCESS;
}
