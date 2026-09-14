/* HAMi owns aggregate Pod enforcement. This module does not count allocations. */
#include <cuda.h>
#include <cuda_runtime.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "resource-backend.h"
#include "device-management.h"

static int device = -1;
static CUcontext primary;
static uint64_t quota_bytes;
static int initialized;
static _Thread_local unsigned int primary_depth;

static void context_check(CUresult result) {
    if (result != CUDA_SUCCESS) {
        fprintf(stderr, "HAMi context operation failed (%d); terminating this RPC server\n", (int)result);
        exit(EXIT_FAILURE);
    }
}
static int unsupported(void) {
    fprintf(stderr, "HAMi does not support in-process quota/context replacement; use a new VMI allocation\n");
    return -1;
}
static int change_sm(uint32_t n) { (void)n; return unsupported(); }
static int set_limit(uint64_t n) { (void)n; return unsupported(); }
static int set_config(uint32_t n, uint64_t m) { (void)n; (void)m; return unsupported(); }
static void set_device(int d) { device = d; }
static int get_device(void) { return device; }
static void no_usage(uint64_t n) { (void)n; }
static void no_change(void) {}
static int allow_alloc(uint64_t n) { (void)n; return initialized; }

int flyt_hami_memory(uint64_t *available, uint64_t *total) {
    *available = 0; *total = 0;
    if (!initialized) return cudaErrorInitializationError;
    size_t free_bytes = 0, total_bytes = 0;
    cudaError_t err = cudaMemGetInfo(&free_bytes, &total_bytes);
    if (err != cudaSuccess) {
        fprintf(stderr, "HAMi memory observation failed: %s\n", cudaGetErrorString(err));
        return err;
    }
    if (!total_bytes || total_bytes > quota_bytes || free_bytes > total_bytes) {
        fprintf(stderr, "HAMi memory observation exceeds the configured Pod limit or is inconsistent\n");
        return cudaErrorUnknown;
    }
    *available = free_bytes; *total = total_bytes;
    return cudaSuccess;
}
static uint64_t get_limit(void) {
    uint64_t available, total; return flyt_hami_memory(&available, &total) == cudaSuccess ? total : 0;
}
static uint64_t get_free(void) {
    uint64_t available, total; return flyt_hami_memory(&available, &total) == cudaSuccess ? available : 0;
}
static int init(uint32_t reported_sm, uint64_t memory) {
    if (initialized) return -1;
    const char *value = getenv("FLYT_MEMORY_BYTES");
    if (!value || !*value || strspn(value,"0123456789") != strlen(value)) return -1;
    char *end; errno = 0; unsigned long long expected = strtoull(value, &end, 10);
    if (errno || *end || expected != memory || memory < 256ULL*1024*1024 || memory > 1048576ULL*1024*1024 || device != 0) return -1;
    FILE *maps = fopen("/proc/self/maps", "r");
    if (!maps) return -1;
    char line[4096]; int loaded = 0, client = 0;
    while (fgets(line, sizeof(line), maps)) {
        if (strstr(line,"libvgpu.so")) loaded = 1;
        if (strstr(line,"cricket-client")) client = 1;
    }
    fclose(maps);
    if (!loaded || client) return -1;
    int count = 0; struct cudaDeviceProp prop;
    if (cudaGetDeviceCount(&count) != cudaSuccess || count != 1 || cudaGetDeviceProperties(&prop,device) != cudaSuccess) return -1;
    char uuid[41]; const unsigned char *b = (const unsigned char *)prop.uuid.bytes;
    snprintf(uuid,sizeof(uuid),"GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9],b[10],b[11],b[12],b[13],b[14],b[15]);
    const char *expected_uuid = getenv("FLYT_GPU_UUID");
    if (!expected_uuid || strcmp(uuid,expected_uuid) || prop.multiProcessorCount <= 0 || reported_sm != (uint32_t)prop.multiProcessorCount) return -1;
    if (cuCtxGetCurrent(&primary) != CUDA_SUCCESS || !primary) return -1;
    quota_bytes = memory;
    cur_num_sm_cores = prop.multiProcessorCount; /* CUDA capability, never quota percentage. */
    if (init_device_management(device) != 0) return -1;
    initialized = 1;
    uint64_t available, total;
    if (flyt_hami_memory(&available,&total) != cudaSuccess) { initialized = 0; return -1; }
    fprintf(stderr,"HAMi backend initialized: UUID=%s capability_sm=%u configured_bytes=%llu observed_total=%llu; enforcement unvalidated\n",
        uuid,reported_sm,(unsigned long long)memory,(unsigned long long)total);
    return 0;
}
static void push_primary(void) {
    if (!initialized) exit(EXIT_FAILURE);
    /* Preserve existing API synchronization semantics; do not replace/destroy the context. */
    (void)cudaDeviceSynchronize();
    context_check(cuCtxPushCurrent(primary)); ++primary_depth;
}
static void pop_primary(void) {
    CUcontext popped = NULL;
    if (!primary_depth) exit(EXIT_FAILURE);
    context_check(cuCtxPopCurrent(&popped)); --primary_depth;
    if (popped != primary) exit(EXIT_FAILURE);
}
static void set_exec(void) {
    if (!initialized) exit(EXIT_FAILURE);
    (void)cudaDeviceSynchronize(); context_check(cuCtxSetCurrent(primary));
}
const FlytResourceBackend flyt_hami_ops = {
    change_sm,set_device,get_device,set_limit,get_limit,allow_alloc,get_free,
    no_usage,no_usage,no_change,set_config,init,push_primary,pop_primary,set_exec
};
