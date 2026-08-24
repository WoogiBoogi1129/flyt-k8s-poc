#include <cuda_runtime.h>

#include <array>
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

namespace {

using Clock = std::chrono::steady_clock;

double seconds_between(Clock::time_point start, Clock::time_point finish) {
  return std::chrono::duration<double>(finish - start).count();
}

bool all_equal(const std::array<unsigned char, 64> &sample,
               unsigned char expected) {
  for (unsigned char value : sample) {
    if (value != expected) return false;
  }
  return true;
}

}  // namespace

int main(int argc, char **argv) {
  const unsigned long long copies =
      argc > 1 ? std::strtoull(argv[1], nullptr, 10) : 9200ULL;
  const unsigned long long warmup_copies =
      argc > 2 ? std::strtoull(argv[2], nullptr, 10) : 64ULL;
  constexpr size_t bytes = 256ULL * 1024ULL * 1024ULL;
  constexpr unsigned char pattern = 17;
  if (copies == 0) return EXIT_FAILURE;

  CUDA_OK(cudaSetDevice(0));
  void *source = nullptr;
  void *destination = nullptr;
  CUDA_OK(cudaMalloc(&source, bytes));
  CUDA_OK(cudaMalloc(&destination, bytes));
  CUDA_OK(cudaMemset(source, pattern, bytes));
  CUDA_OK(cudaMemset(destination, 0, bytes));

  std::array<unsigned char, 64> sample_start{};
  std::array<unsigned char, 64> sample_middle{};
  std::array<unsigned char, 64> sample_end{};

  // The D2H copy is host-synchronous and ordered after the preceding default
  // stream work.  It makes initialization completion independent of Flyt's
  // current cudaDeviceSynchronize implementation.
  CUDA_OK(cudaMemcpy(sample_start.data(), source, sample_start.size(),
                     cudaMemcpyDeviceToHost));
  if (!all_equal(sample_start, pattern)) {
    std::fprintf(stderr, "source initialization validation failed\n");
    return EXIT_FAILURE;
  }

  for (unsigned long long index = 0; index < warmup_copies; ++index) {
    CUDA_OK(cudaMemcpy(destination, source, bytes,
                       cudaMemcpyDeviceToDevice));
  }
  CUDA_OK(cudaMemcpy(sample_start.data(), destination, sample_start.size(),
                     cudaMemcpyDeviceToHost));
  if (!all_equal(sample_start, pattern)) {
    std::fprintf(stderr, "warmup validation failed\n");
    return EXIT_FAILURE;
  }

  const auto start = Clock::now();
  for (unsigned long long index = 0; index < copies; ++index) {
    CUDA_OK(cudaMemcpy(destination, source, bytes,
                       cudaMemcpyDeviceToDevice));
  }
  const auto enqueue_finish = Clock::now();

  // Do not rely on Flyt's cudaDeviceSynchronize here.  These small D2H reads
  // force completion of the D2D queue and validate three parts of the output.
  CUDA_OK(cudaMemcpy(sample_start.data(), destination, sample_start.size(),
                     cudaMemcpyDeviceToHost));
  CUDA_OK(cudaMemcpy(sample_middle.data(),
                     static_cast<unsigned char *>(destination) + bytes / 2,
                     sample_middle.size(), cudaMemcpyDeviceToHost));
  CUDA_OK(cudaMemcpy(sample_end.data(),
                     static_cast<unsigned char *>(destination) + bytes -
                         sample_end.size(),
                     sample_end.size(), cudaMemcpyDeviceToHost));
  const auto completion_finish = Clock::now();

  const bool checksum_ok = all_equal(sample_start, pattern) &&
                           all_equal(sample_middle, pattern) &&
                           all_equal(sample_end, pattern);

  CUDA_OK(cudaFree(destination));
  CUDA_OK(cudaFree(source));
  const auto cleanup_finish = Clock::now();

  const double enqueue_s = seconds_between(start, enqueue_finish);
  const double completion_s = seconds_between(start, completion_finish);
  const double cleanup_s = seconds_between(start, cleanup_finish);
  const double drain_s = completion_s - enqueue_s;
  const double gib = static_cast<double>(bytes) * copies /
                     (1024.0 * 1024.0 * 1024.0);

  std::printf(
      "copies=%llu warmup_copies=%llu bytes_per_copy=%zu gib=%.6f "
      "enqueue_s=%.6f drain_s=%.6f completion_s=%.6f cleanup_s=%.6f "
      "enqueue_gib_s=%.6f completion_gib_s=%.6f checksum_ok=%s\n",
      copies, warmup_copies, bytes, gib, enqueue_s, drain_s, completion_s,
      cleanup_s, gib / enqueue_s, gib / completion_s,
      checksum_ok ? "true" : "false");
  std::printf("bandwidth_v2_status=%s\n", checksum_ok ? "pass" : "fail");
  return checksum_ok ? EXIT_SUCCESS : EXIT_FAILURE;
}
