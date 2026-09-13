#include <cuda_runtime.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

// Standalone CUDA: no Flyt client, RPC, or MPS startup code belongs here.
using Clock = std::chrono::steady_clock;
constexpr size_t MiB = 1024ULL * 1024ULL;

static void check(cudaError_t error, const char *operation) {
  if (error != cudaSuccess)
    throw std::runtime_error(std::string(operation) + ": " + cudaGetErrorString(error));
}

static int integer(const char *value, int low, int high) {
  size_t end = 0;
  int result = std::stoi(value, &end);
  if (value[end] != '\0' || result < low || result > high)
    throw std::runtime_error("argument outside allowed range");
  return result;
}

static std::string device_uuid(const cudaDeviceProp &prop) {
  char text[41];
  auto *b = reinterpret_cast<const unsigned char *>(prop.uuid.bytes);
  std::snprintf(text, sizeof(text),
                "GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
                b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
  return text;
}

static std::string guard() {
  const char *expected = std::getenv("EXPECTED_GPU_UUID");
  if (!expected) throw std::runtime_error("EXPECTED_GPU_UUID is required");
  for (const char *name : {"CUDA_MPS_PIPE_DIRECTORY", "CUDA_MPS_ACTIVE_THREAD_PERCENTAGE",
                           "CUDA_MPS_ENABLE_PER_CTX_DEVICE_MULTIPROCESSOR_PARTITIONING"}) {
    if (std::getenv(name)) throw std::runtime_error("unexpected MPS environment");
  }
  int count = 0;
  check(cudaGetDeviceCount(&count), "device count");
  if (count != 1) throw std::runtime_error("expected exactly one CUDA-visible GPU");
  cudaDeviceProp prop{};
  check(cudaGetDeviceProperties(&prop, 0), "device properties");
  const auto actual = device_uuid(prop);
  if (actual != expected) throw std::runtime_error("CUDA GPU UUID does not match target");
  std::ifstream maps("/proc/self/maps");
  std::string line;
  bool hami_loaded = false;
  while (std::getline(maps, line)) {
    if (line.find("libvgpu.so") != std::string::npos) hami_loaded = true;
    if (line.find("cricket") != std::string::npos || line.find("libflyt") != std::string::npos)
      throw std::runtime_error("Flyt/Cricket library unexpectedly loaded");
  }
  if (!hami_loaded) throw std::runtime_error("HAMi libvgpu.so not mapped; quota test invalid");
  check(cudaSetDevice(0), "set device");
  check(cudaFree(nullptr), "initialize context");
  return actual;
}

static int memory_test(const std::string &uuid, int quota, int chunk, int headroom) {
  // Context overhead is allowed explicitly, never inferred from an arbitrary OOM.
  const size_t minimum = static_cast<size_t>(quota - headroom);
  std::vector<void *> pointers;
  size_t allocated = 0;
  cudaError_t error = cudaSuccess;
  while (allocated + chunk <= static_cast<size_t>(quota + chunk)) {
    void *ptr = nullptr;
    error = cudaMalloc(&ptr, static_cast<size_t>(chunk) * MiB);
    if (error != cudaSuccess) break;
    pointers.push_back(ptr);
    allocated += chunk;
    check(cudaMemset(ptr, 0x5a, static_cast<size_t>(chunk) * MiB), "touch allocation");
    check(cudaDeviceSynchronize(), "touch synchronize");
  }
  bool boundary = error == cudaErrorMemoryAllocation && allocated >= minimum &&
                  allocated <= static_cast<size_t>(quota);
  for (auto ptr : pointers) check(cudaFree(ptr), "free allocation");
  check(cudaDeviceSynchronize(), "free synchronize");
  void *reused = nullptr;
  auto reuse_error = cudaMalloc(&reused, static_cast<size_t>(chunk) * MiB);
  if (reuse_error == cudaSuccess) check(cudaFree(reused), "free reused allocation");
  bool pass = boundary && reuse_error == cudaSuccess;
  std::printf("{\"schema\":1,\"test\":\"memory\",\"gpu_uuid\":\"%s\","
              "\"quota_mib\":%d,\"chunk_mib\":%d,\"headroom_mib\":%d,"
              "\"allocated_mib\":%zu,\"cuda_error\":%d,\"reuse_ok\":%s,\"status\":\"%s\"}\n",
              uuid.c_str(), quota, chunk, headroom, allocated, static_cast<int>(error),
              reuse_error == cudaSuccess ? "true" : "false", pass ? "PASS" : "FAIL");
  return pass ? 0 : 1;
}

__global__ void burn(float *out) {
  const int i = blockIdx.x * blockDim.x + threadIdx.x;
  float x = 0.1f + static_cast<float>(i % 31) * 0.001f;
  #pragma unroll 1
  for (int step = 0; step < 8192; ++step) x = fmaf(x, 0.999999f, 0.000001f);
  out[i] = x;
}

static int compute_test(const std::string &uuid, int seconds, int warmup, int cores) {
  // Fixed launch geometry across quotas. Host wall time includes HAMi throttling.
  constexpr int blocks = 1024, threads = 256;
  float *out = nullptr;
  check(cudaMalloc(&out, blocks * threads * sizeof(float)), "compute allocation");
  auto launch = [&]() {
    burn<<<blocks, threads>>>(out);
    check(cudaGetLastError(), "kernel launch");
    check(cudaDeviceSynchronize(), "kernel synchronize");
  };
  auto start = Clock::now();
  while (std::chrono::duration<double>(Clock::now() - start).count() < warmup) launch();
  start = Clock::now();
  unsigned long long launches = 0;
  while (std::chrono::duration<double>(Clock::now() - start).count() < seconds) {
    launch();
    ++launches;
  }
  double elapsed = std::chrono::duration<double>(Clock::now() - start).count();
  std::vector<float> host(blocks * threads);
  check(cudaMemcpy(host.data(), out, host.size() * sizeof(float), cudaMemcpyDeviceToHost), "read output");
  check(cudaFree(out), "free compute buffer");
  bool valid = std::all_of(host.begin(), host.end(), [](float x) {
    return std::isfinite(x) && x > 0.0f && x < 1.0f;
  });
  std::printf("{\"schema\":1,\"test\":\"compute\",\"gpu_uuid\":\"%s\","
              "\"cores\":%d,\"blocks\":%d,\"threads\":%d,\"iterations\":8192,"
              "\"warmup_seconds\":%d,\"requested_seconds\":%d,\"elapsed_seconds\":%.6f,"
              "\"launches\":%llu,\"launches_per_second\":%.6f,\"status\":\"%s\"}\n",
              uuid.c_str(), cores, blocks, threads, warmup, seconds, elapsed, launches,
              launches / elapsed, valid && launches > 0 ? "PASS" : "FAIL");
  return valid && launches > 0 ? 0 : 1;
}

int main(int argc, char **argv) {
  try {
    if (argc != 5) throw std::runtime_error(
        "usage: probe memory quota_mib chunk_mib headroom_mib | compute seconds warmup_seconds cores");
    std::string mode = argv[1];
    if (mode == "memory") {
      int quota = integer(argv[2], 1024, 131072);
      int chunk = integer(argv[3], 1, 1024);
      int headroom = integer(argv[4], 1, quota / 2);
      if (chunk > headroom) throw std::runtime_error("chunk exceeds headroom");
      return memory_test(guard(), quota, chunk, headroom);
    }
    if (mode == "compute")
      return compute_test(guard(), integer(argv[2], 5, 300), integer(argv[3], 1, 60), integer(argv[4], 1, 100));
    throw std::runtime_error("unknown probe mode");
  } catch (const std::exception &error) {
    std::fprintf(stderr, "probe_error=%s\n", error.what());
    std::puts("{\"schema\":1,\"test\":\"environment\",\"status\":\"ERROR\"}");
    return 2;
  }
}
