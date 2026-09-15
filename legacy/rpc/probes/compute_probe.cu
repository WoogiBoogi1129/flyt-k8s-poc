#include <cuda_runtime.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#define CUDA_OK(call)                                                            \
  do {                                                                           \
    cudaError_t error = (call);                                                   \
    if (error != cudaSuccess) {                                                   \
      std::fprintf(stderr, "%s failed: %s\n", #call, cudaGetErrorString(error)); \
      return EXIT_FAILURE;                                                        \
    }                                                                            \
  } while (0)

__global__ void fma_burn(float *output, unsigned long long iterations) {
  float value = 1.0F + static_cast<float>((blockIdx.x * blockDim.x + threadIdx.x) & 31) * 1.0e-6F;
  #pragma unroll 1
  for (unsigned long long i = 0; i < iterations; ++i) {
    value = fmaf(value, 1.000000119F, 0.000000119F);
  }
  output[blockIdx.x * blockDim.x + threadIdx.x] = value;
}

int main(int argc, char **argv) {
  const unsigned long long iterations = argc > 1 ? std::strtoull(argv[1], nullptr, 10) : 200000ULL;
  int sm_count = 0;
  CUDA_OK(cudaSetDevice(0));
  CUDA_OK(cudaDeviceGetAttribute(&sm_count, cudaDevAttrMultiProcessorCount, 0));
  const int blocks = argc > 2 ? std::atoi(argv[2]) : 6016;
  if (blocks < 1 || iterations < 1) {
    std::fprintf(stderr, "usage: %s [iterations] [fixed_blocks]\n", argv[0]);
    return EXIT_FAILURE;
  }
  constexpr int threads = 256;
  const size_t elements = static_cast<size_t>(blocks) * threads;
  float *device_output = nullptr;
  CUDA_OK(cudaMalloc(&device_output, elements * sizeof(float)));

  const auto started = std::chrono::steady_clock::now();
  fma_burn<<<blocks, threads>>>(device_output, iterations);
  CUDA_OK(cudaGetLastError());
  CUDA_OK(cudaDeviceSynchronize());
  const auto finished = std::chrono::steady_clock::now();

  std::vector<float> host_output(elements);
  CUDA_OK(cudaMemcpy(host_output.data(), device_output, elements * sizeof(float), cudaMemcpyDeviceToHost));
  double checksum = 0.0;
  for (float value : host_output) checksum += value;
  const bool finite = std::isfinite(checksum);
  const double seconds = std::chrono::duration<double>(finished - started).count();
  const double operations = static_cast<double>(elements) * static_cast<double>(iterations) * 2.0;

  std::printf("reported_sm=%d blocks=%d threads=%d iterations=%llu\n", sm_count, blocks, threads, iterations);
  std::printf("runtime_s=%.6f nominal_gflops=%.3f checksum=%.9e checksum_ok=%s\n",
              seconds, operations / seconds / 1.0e9, checksum, finite ? "true" : "false");
  cudaFree(device_output);
  return finite ? EXIT_SUCCESS : EXIT_FAILURE;
}
