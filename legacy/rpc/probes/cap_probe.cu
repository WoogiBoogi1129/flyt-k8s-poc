#include <cuda_runtime.h>

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

__global__ void vector_add(const float *a, const float *b, float *c, int n) {
  int index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index < n) c[index] = a[index] + b[index];
}

int main() {
  int device_count = 0;
  int sm_count = 0;
  cudaDeviceProp properties{};
  CUDA_OK(cudaGetDeviceCount(&device_count));
  if (device_count < 1) {
    std::fprintf(stderr, "device_count=%d\n", device_count);
    return EXIT_FAILURE;
  }
  CUDA_OK(cudaSetDevice(0));
  CUDA_OK(cudaDeviceGetAttribute(&sm_count, cudaDevAttrMultiProcessorCount, 0));
  CUDA_OK(cudaGetDeviceProperties(&properties, 0));

  constexpr int n = 1 << 20;
  constexpr size_t bytes = n * sizeof(float);
  std::vector<float> host_a(n, 1.25F), host_b(n, 2.75F), host_c(n, 0.0F);
  float *device_a = nullptr, *device_b = nullptr, *device_c = nullptr;
  CUDA_OK(cudaMalloc(&device_a, bytes));
  CUDA_OK(cudaMalloc(&device_b, bytes));
  CUDA_OK(cudaMalloc(&device_c, bytes));
  CUDA_OK(cudaMemcpy(device_a, host_a.data(), bytes, cudaMemcpyHostToDevice));
  CUDA_OK(cudaMemcpy(device_b, host_b.data(), bytes, cudaMemcpyHostToDevice));
  vector_add<<<(n + 255) / 256, 256>>>(device_a, device_b, device_c, n);
  CUDA_OK(cudaGetLastError());
  CUDA_OK(cudaDeviceSynchronize());
  CUDA_OK(cudaMemcpy(host_c.data(), device_c, bytes, cudaMemcpyDeviceToHost));

  double checksum = 0.0;
  for (float value : host_c) checksum += value;
  const double expected = 4.0 * n;
  const bool checksum_ok = checksum == expected;

  std::printf("device_count=%d\n", device_count);
  std::printf("gpu_name=%s\n", properties.name);
  std::printf("reported_sm=%d\n", sm_count);
  std::printf("checksum=%.0f expected=%.0f checksum_ok=%s\n", checksum, expected,
              checksum_ok ? "true" : "false");

  cudaFree(device_c);
  cudaFree(device_b);
  cudaFree(device_a);
  return checksum_ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
