#include <cublas_v2.h>
#include <cuda_runtime.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#define CUDA_OK(call)                                                         \
  do {                                                                        \
    cudaError_t error = (call);                                                \
    if (error != cudaSuccess) {                                                \
      std::fprintf(stderr, "%s failed: %d %s\n", #call,                    \
                   static_cast<int>(error), cudaGetErrorString(error));        \
      return EXIT_FAILURE;                                                     \
    }                                                                         \
  } while (0)

#define CUBLAS_OK(call)                                                       \
  do {                                                                        \
    cublasStatus_t status = (call);                                            \
    if (status != CUBLAS_STATUS_SUCCESS) {                                     \
      std::fprintf(stderr, "%s failed: %d\n", #call,                       \
                   static_cast<int>(status));                                  \
      return EXIT_FAILURE;                                                     \
    }                                                                         \
  } while (0)

__global__ void add_one(float *values, size_t count) {
  size_t index = static_cast<size_t>(blockIdx.x) * blockDim.x + threadIdx.x;
  if (index < count) values[index] += 1.0F;
}

int main() {
  int count = 0;
  CUDA_OK(cudaGetDeviceCount(&count));
  if (count != 1) {
    std::fprintf(stderr, "expected one device, got %d\n", count);
    return EXIT_FAILURE;
  }
  CUDA_OK(cudaSetDevice(0));
  int current_device = -1;
  CUDA_OK(cudaGetDevice(&current_device));
  cudaDeviceProp properties{};
  CUDA_OK(cudaGetDeviceProperties(&properties, 0));
  size_t free_bytes = 0;
  size_t total_bytes = 0;
  CUDA_OK(cudaMemGetInfo(&free_bytes, &total_bytes));

  constexpr size_t elements = 256 * 1024;
  constexpr size_t bytes = elements * sizeof(float);
  std::vector<float> host(elements, 0.0F);
  float *device = nullptr;
  CUDA_OK(cudaMalloc(&device, bytes));
  CUDA_OK(cudaMemset(device, 0, bytes));

  cudaStream_t stream = nullptr;
  cudaEvent_t start = nullptr;
  cudaEvent_t finish = nullptr;
  CUDA_OK(cudaStreamCreate(&stream));
  CUDA_OK(cudaEventCreate(&start));
  CUDA_OK(cudaEventCreate(&finish));
  CUDA_OK(cudaMemsetAsync(device, 0, bytes, stream));
  CUDA_OK(cudaMemcpyAsync(device, host.data(), bytes, cudaMemcpyHostToDevice,
                          stream));
  CUDA_OK(cudaEventRecord(start, stream));
  add_one<<<1024, 256, 0, stream>>>(device, elements);
  CUDA_OK(cudaPeekAtLastError());
  CUDA_OK(cudaGetLastError());
  CUDA_OK(cudaEventRecord(finish, stream));
  CUDA_OK(cudaEventSynchronize(finish));
  float elapsed_ms = 0.0F;
  CUDA_OK(cudaEventElapsedTime(&elapsed_ms, start, finish));
  CUDA_OK(cudaMemcpyAsync(host.data(), device, bytes, cudaMemcpyDeviceToHost,
                          stream));
  CUDA_OK(cudaStreamSynchronize(stream));
  for (float value : host) {
    if (value != 1.0F) {
      std::fprintf(stderr, "runtime checksum mismatch: %.9g\n", value);
      return EXIT_FAILURE;
    }
  }

  constexpr int side = 64;
  constexpr size_t matrix_elements = side * side;
  std::vector<float> matrix_a(matrix_elements, 1.0F);
  std::vector<float> matrix_b(matrix_elements, 1.0F);
  std::vector<float> matrix_c(matrix_elements, 0.0F);
  float *device_a = nullptr;
  float *device_b = nullptr;
  float *device_c = nullptr;
  CUDA_OK(cudaMalloc(&device_a, matrix_elements * sizeof(float)));
  CUDA_OK(cudaMalloc(&device_b, matrix_elements * sizeof(float)));
  CUDA_OK(cudaMalloc(&device_c, matrix_elements * sizeof(float)));
  CUDA_OK(cudaMemcpy(device_a, matrix_a.data(), matrix_elements * sizeof(float),
                     cudaMemcpyHostToDevice));
  CUDA_OK(cudaMemcpy(device_b, matrix_b.data(), matrix_elements * sizeof(float),
                     cudaMemcpyHostToDevice));

  cublasHandle_t handle = nullptr;
  CUBLAS_OK(cublasCreate(&handle));
  const float alpha = 1.0F;
  const float beta = 0.0F;
  CUBLAS_OK(cublasSgemm(handle, CUBLAS_OP_N, CUBLAS_OP_N, side, side, side,
                        &alpha, device_a, side, device_b, side, &beta, device_c,
                        side));
  CUDA_OK(cudaDeviceSynchronize());
  CUDA_OK(cudaMemcpy(matrix_c.data(), device_c,
                     matrix_elements * sizeof(float), cudaMemcpyDeviceToHost));
  CUBLAS_OK(cublasDestroy(handle));

  double cublas_checksum = 0.0;
  for (float value : matrix_c) cublas_checksum += value;
  const double expected_checksum =
      static_cast<double>(side) * side * side;
  if (std::fabs(cublas_checksum - expected_checksum) > 0.5) {
    std::fprintf(stderr, "cublas checksum mismatch: %.9f expected %.9f\n",
                 cublas_checksum, expected_checksum);
    return EXIT_FAILURE;
  }

  cudaError_t invalid_device = cudaSetDevice(count);
  if (invalid_device != cudaErrorInvalidDevice) {
    std::fprintf(stderr, "invalid-device status mismatch: %d %s\n",
                 static_cast<int>(invalid_device),
                 cudaGetErrorString(invalid_device));
    return EXIT_FAILURE;
  }
  (void)cudaGetLastError();
  CUDA_OK(cudaSetDevice(0));

  CUDA_OK(cudaFree(device_c));
  CUDA_OK(cudaFree(device_b));
  CUDA_OK(cudaFree(device_a));
  CUDA_OK(cudaEventDestroy(finish));
  CUDA_OK(cudaEventDestroy(start));
  CUDA_OK(cudaStreamDestroy(stream));
  CUDA_OK(cudaFree(device));
  CUDA_OK(cudaDeviceSynchronize());

  std::printf("device_count=%d current_device=%d reported_sm=%d total_bytes=%zu "
              "free_bytes=%zu\n",
              count, current_device, properties.multiProcessorCount,
              total_bytes, free_bytes);
  std::printf("runtime_checksum_ok=true event_elapsed_ms=%.6f\n", elapsed_ms);
  std::printf("cublas_checksum=%.0f cublas_checksum_ok=true\n",
              cublas_checksum);
  std::printf("invalid_device_error=%d invalid_device_semantics_ok=true\n",
              static_cast<int>(invalid_device));
  std::printf("core_api_status=pass\n");
  return EXIT_SUCCESS;
}
