#include <cuda_runtime.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <vector>

#define CUDA_OK(call)                                                         \
  do {                                                                        \
    cudaError_t error = (call);                                                \
    if (error != cudaSuccess) {                                                \
      std::fprintf(stderr, "%s failed: %s\n", #call,                       \
                   cudaGetErrorString(error));                                 \
      std::exit(EXIT_FAILURE);                                                 \
    }                                                                         \
  } while (0)

__global__ void empty_kernel() {}

template <typename Function>
double measure_ms(Function function) {
  const auto start = std::chrono::steady_clock::now();
  function();
  const auto finish = std::chrono::steady_clock::now();
  return std::chrono::duration<double, std::milli>(finish - start).count();
}

int main() {
  constexpr int launch_iterations = 10000;
  constexpr int sync_iterations = 1000;
  constexpr int allocation_iterations = 1000;
  constexpr int small_copy_iterations = 1000;
  constexpr int large_copy_iterations = 16;
  constexpr size_t small_bytes = 4096;
  constexpr size_t large_bytes = 64ULL * 1024ULL * 1024ULL;

  CUDA_OK(cudaSetDevice(0));
  empty_kernel<<<1, 1>>>();
  CUDA_OK(cudaDeviceSynchronize());

  const double launch_ms = measure_ms([&]() {
    for (int i = 0; i < launch_iterations; ++i) empty_kernel<<<1, 1>>>();
    CUDA_OK(cudaDeviceSynchronize());
  });

  const double sync_ms = measure_ms([&]() {
    for (int i = 0; i < sync_iterations; ++i) CUDA_OK(cudaDeviceSynchronize());
  });

  const double allocation_ms = measure_ms([&]() {
    for (int i = 0; i < allocation_iterations; ++i) {
      void *pointer = nullptr;
      CUDA_OK(cudaMalloc(&pointer, 1024 * 1024));
      CUDA_OK(cudaFree(pointer));
    }
  });

  std::vector<unsigned char> small_host(small_bytes, 7);
  std::vector<unsigned char> large_host(large_bytes, 11);
  void *small_device = nullptr;
  void *large_device = nullptr;
  CUDA_OK(cudaMalloc(&small_device, small_bytes));
  CUDA_OK(cudaMalloc(&large_device, large_bytes));

  const double small_copy_ms = measure_ms([&]() {
    for (int i = 0; i < small_copy_iterations; ++i) {
      CUDA_OK(cudaMemcpy(small_device, small_host.data(), small_bytes,
                         cudaMemcpyHostToDevice));
      CUDA_OK(cudaMemcpy(small_host.data(), small_device, small_bytes,
                         cudaMemcpyDeviceToHost));
    }
  });
  const double large_copy_ms = measure_ms([&]() {
    for (int i = 0; i < large_copy_iterations; ++i) {
      CUDA_OK(cudaMemcpy(large_device, large_host.data(), large_bytes,
                         cudaMemcpyHostToDevice));
      CUDA_OK(cudaMemcpy(large_host.data(), large_device, large_bytes,
                         cudaMemcpyDeviceToHost));
    }
  });

  CUDA_OK(cudaFree(large_device));
  CUDA_OK(cudaFree(small_device));
  CUDA_OK(cudaDeviceSynchronize());

  std::printf("empty_launch_count=%d total_ms=%.6f per_call_us=%.6f\n",
              launch_iterations, launch_ms,
              launch_ms * 1000.0 / launch_iterations);
  std::printf("device_sync_count=%d total_ms=%.6f per_call_us=%.6f\n",
              sync_iterations, sync_ms, sync_ms * 1000.0 / sync_iterations);
  std::printf("malloc_free_count=%d total_ms=%.6f per_pair_us=%.6f\n",
              allocation_iterations, allocation_ms,
              allocation_ms * 1000.0 / allocation_iterations);
  std::printf("copy_4k_roundtrips=%d total_ms=%.6f per_roundtrip_us=%.6f\n",
              small_copy_iterations, small_copy_ms,
              small_copy_ms * 1000.0 / small_copy_iterations);
  const double large_gib =
      static_cast<double>(large_bytes) * large_copy_iterations * 2.0 /
      (1024.0 * 1024.0 * 1024.0);
  std::printf("copy_64m_roundtrips=%d total_ms=%.6f effective_gib_s=%.6f\n",
              large_copy_iterations, large_copy_ms,
              large_gib / (large_copy_ms / 1000.0));
  std::printf("api_latency_status=pass\n");
  return EXIT_SUCCESS;
}
