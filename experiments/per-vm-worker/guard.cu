// Startup inventory guard, not an isolation or compatibility benchmark.
#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>

int main() {
    const char *expected = getenv("FLYT_GPU_UUID");
    const char *backend = getenv("FLYT_RESOURCE_BACKEND");
    if (!expected || !backend || strcmp(backend, "hami")) return 1;
    FILE *maps = fopen("/proc/self/maps", "r");
    if (!maps) return 2;
    bool hami = false, client = false;
    char line[4096];
    while (fgets(line, sizeof(line), maps)) {
        hami |= strstr(line, "libvgpu.so") != nullptr;
        client |= strstr(line, "cricket-client") != nullptr;
    }
    fclose(maps);
    if (!hami || client) {
        fprintf(stderr, "Worker must load HAMi and must not preload the Cricket client\n");
        return 3;
    }
    int count = 0;
    if (cudaGetDeviceCount(&count) != cudaSuccess || count != 1) return 4;
    cudaDeviceProp prop{};
    if (cudaGetDeviceProperties(&prop, 0) != cudaSuccess) return 5;
    char uuid[41];
    auto b = reinterpret_cast<const unsigned char *>(prop.uuid.bytes);
    snprintf(uuid, sizeof(uuid), "GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
             b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
             b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
    if (strcmp(expected, uuid) || prop.multiProcessorCount <= 0) {
        fprintf(stderr, "Visible GPU does not match the configured Worker UUID\n");
        return 6;
    }
    printf("FLYT_GUARD_SM=%d\n", prop.multiProcessorCount);
    return 0;
}
