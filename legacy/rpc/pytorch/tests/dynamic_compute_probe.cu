#include <cuda_runtime.h>

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>

#define CUDA_OK(call)                                                            \
  do {                                                                           \
    cudaError_t error = (call);                                                   \
    if (error != cudaSuccess) {                                                   \
      std::fprintf(stderr, "%s failed: %s\n", #call, cudaGetErrorString(error)); \
      return EXIT_FAILURE;                                                        \
    }                                                                            \
  } while (0)

__global__ void fma_burn(float *output, unsigned long long iterations) {
  float value = 1.0F +
      static_cast<float>((blockIdx.x * blockDim.x + threadIdx.x) & 31) *
          1.0e-6F;
#pragma unroll 1
  for (unsigned long long i = 0; i < iterations; ++i) {
    value = fmaf(value, 1.000000119F, 0.000000119F);
  }
  output[blockIdx.x * blockDim.x + threadIdx.x] = value;
}

int main(int argc, char **argv) {
  const int seconds = argc > 1 ? std::atoi(argv[1]) : 300;
  const char *output_path = argc > 2 ? argv[2] : nullptr;
  const unsigned long long kernel_iterations =
      argc > 3 ? std::strtoull(argv[3], nullptr, 10) : 200000ULL;
  const int blocks = argc > 4 ? std::atoi(argv[4]) : 1024;
  constexpr int threads = 256;
  if (seconds < 1 || kernel_iterations < 1 || blocks < 1) {
    std::fprintf(stderr,
                 "usage: %s [seconds] [output.jsonl] [iterations] [blocks]\n",
                 argv[0]);
    return EXIT_FAILURE;
  }

  FILE *output_file = output_path ? std::fopen(output_path, "w") : nullptr;
  if (output_path && !output_file) {
    std::perror("fopen");
    return EXIT_FAILURE;
  }

  CUDA_OK(cudaSetDevice(0));
  const size_t elements = static_cast<size_t>(blocks) * threads;
  float *device_output = nullptr;
  CUDA_OK(cudaMalloc(&device_output, elements * sizeof(float)));
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::seconds(seconds);

  int iteration = 0;
  while (std::chrono::steady_clock::now() < deadline) {
    const auto started = std::chrono::steady_clock::now();
    fma_burn<<<blocks, threads>>>(device_output, kernel_iterations);
    CUDA_OK(cudaGetLastError());
    CUDA_OK(cudaDeviceSynchronize());
    const auto finished = std::chrono::steady_clock::now();
    const double epoch_s = std::chrono::duration<double>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    const double latency_s =
        std::chrono::duration<double>(finished - started).count();
    const double operations = static_cast<double>(elements) *
                              static_cast<double>(kernel_iterations) * 2.0;
    char record[512];
    std::snprintf(record, sizeof(record),
                  "{\"iteration\":%d,\"epoch_s\":%.6f,"
                  "\"latency_s\":%.9f,\"loss\":0.0,\"finite\":true,"
                  "\"nominal_gflops\":%.3f}",
                  iteration++, epoch_s, latency_s,
                  operations / latency_s / 1.0e9);
    std::puts(record);
    std::fflush(stdout);
    if (output_file) {
      std::fprintf(output_file, "%s\n", record);
      std::fflush(output_file);
    }
  }

  float sample = 0.0F;
  CUDA_OK(cudaMemcpy(&sample, device_output, sizeof(sample),
                     cudaMemcpyDeviceToHost));
  CUDA_OK(cudaFree(device_output));
  if (output_file) std::fclose(output_file);
  return std::isfinite(sample) ? EXIT_SUCCESS : EXIT_FAILURE;
}
