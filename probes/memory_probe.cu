#include <cuda_runtime.h>

#include <cstdio>
#include <cstdlib>
#include <vector>

int main(int argc, char **argv) {
  const size_t chunk_mib = argc > 1 ? std::strtoull(argv[1], nullptr, 10) : 256ULL;
  const size_t maximum_mib = argc > 2 ? std::strtoull(argv[2], nullptr, 10) : 16384ULL;
  if (chunk_mib == 0 || maximum_mib < chunk_mib) {
    std::fprintf(stderr, "usage: %s [chunk_mib] [maximum_mib]\n", argv[0]);
    return EXIT_FAILURE;
  }

  const size_t chunk_bytes = chunk_mib * 1024ULL * 1024ULL;
  std::vector<void *> allocations;
  size_t allocated_mib = 0;
  cudaError_t last_error = cudaSuccess;

  while (allocated_mib + chunk_mib <= maximum_mib) {
    void *pointer = nullptr;
    last_error = cudaMalloc(&pointer, chunk_bytes);
    if (last_error != cudaSuccess) break;
    allocations.push_back(pointer);
    allocated_mib += chunk_mib;
  }

  std::printf("chunk_mib=%zu allocated_mib=%zu allocation_count=%zu\n",
              chunk_mib, allocated_mib, allocations.size());
  std::printf("last_error=%d last_error_name=%s expected_oom=%s\n",
              static_cast<int>(last_error), cudaGetErrorString(last_error),
              last_error == cudaErrorMemoryAllocation ? "true" : "false");

  for (void *pointer : allocations) cudaFree(pointer);
  cudaDeviceSynchronize();
  return last_error == cudaErrorMemoryAllocation ? EXIT_SUCCESS : EXIT_FAILURE;
}
