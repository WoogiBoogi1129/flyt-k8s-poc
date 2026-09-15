#include "flyt_cuda_exec.h"
#include <cuda_runtime_api.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>

static int worker_guard(void) {
    const char *backend=getenv("FLYT_RESOURCE_BACKEND"),*uuid=getenv("FLYT_GPU_UUID"),*quota=getenv("FLYT_MEMORY_BYTES");
    char line[4096],actual[41],*end;int hami=0,client=0,count=0;size_t free_bytes=0,total=0;
    if(!backend||strcmp(backend,"hami")||!uuid||!quota||getenv("CUDA_MPS_PIPE_DIRECTORY"))return -1;
    errno=0;unsigned long long limit=strtoull(quota,&end,10);if(errno||*end||!limit)return -1;
    FILE *f=fopen("/proc/self/maps","r");if(!f)return -1;
    while(fgets(line,sizeof(line),f)){if(strstr(line,"libvgpu.so"))hami=1;if(strstr(line,"cricket-client")||strstr(line,"libflyt_guest"))client=1;}fclose(f);
    if(!hami||client||cudaGetDeviceCount(&count)!=cudaSuccess||count!=1)return -1;
    struct cudaDeviceProp prop;memset(&prop,0,sizeof(prop));
    if(cudaGetDeviceProperties(&prop,0)!=cudaSuccess)return -1;
    const unsigned char *b=(const unsigned char *)prop.uuid.bytes;
    snprintf(actual,sizeof(actual),"GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",b[0],b[1],b[2],b[3],b[4],b[5],b[6],b[7],b[8],b[9],b[10],b[11],b[12],b[13],b[14],b[15]);
    if(strcmp(uuid,actual)||cudaSetDevice(0)!=cudaSuccess||cudaMemGetInfo(&free_bytes,&total)!=cudaSuccess||!total||total>limit||free_bytes>total)return -1;
    return 0;
}

struct flyt_cuda_runtime { pthread_t owner; };
static atomic_flag runtime_opened = ATOMIC_FLAG_INIT;

static int owned(void *context)
{
    struct flyt_cuda_runtime *r = context;
    return r && pthread_equal(r->owner, pthread_self());
}

static uint32_t get_count(void *context, int32_t *out)
{
    int n = 0;
    cudaError_t error;
    if (!owned(context)) return (uint32_t)cudaErrorInvalidResourceHandle;
    error = cudaGetDeviceCount(&n);
    if (error == cudaSuccess && n != 1) return (uint32_t)cudaErrorInvalidDevice;
    if (error == cudaSuccess) *out = n;
    return (uint32_t)error;
}
static uint32_t get_device(void *context, int32_t *out)
{
    int device = -1;
    cudaError_t error;
    if (!owned(context)) return (uint32_t)cudaErrorInvalidResourceHandle;
    error = cudaGetDevice(&device);
    if (error == cudaSuccess && device != 0) return (uint32_t)cudaErrorInvalidDevice;
    if (error == cudaSuccess) *out = device;
    return (uint32_t)error;
}
static uint32_t set_device(void *context, int32_t device)
{
    if (!owned(context)) return (uint32_t)cudaErrorInvalidResourceHandle;
    if (device != 0) return (uint32_t)cudaErrorInvalidDevice;
    return (uint32_t)cudaSetDevice(0);
}
static uint32_t allocate(void *context, void **out, size_t bytes)
{
    if (!owned(context)) return (uint32_t)cudaErrorInvalidResourceHandle;
    return (uint32_t)cudaMalloc(out, bytes);
}
static uint32_t release(void *context, void *pointer)
{
    if (!owned(context)) return (uint32_t)cudaErrorInvalidResourceHandle;
    return (uint32_t)cudaFree(pointer);
}
static uint32_t copy(void *context, void *dst, const void *src, size_t bytes,
                     uint32_t direction)
{
    enum cudaMemcpyKind kind;
    if (!owned(context)) return (uint32_t)cudaErrorInvalidResourceHandle;
    switch (direction) {
    case FLYT_COPY_HTOD: kind = cudaMemcpyHostToDevice; break;
    case FLYT_COPY_DTOH: kind = cudaMemcpyDeviceToHost; break;
    case FLYT_COPY_DTOD: kind = cudaMemcpyDeviceToDevice; break;
    default: return (uint32_t)cudaErrorInvalidValue;
    }
    /* Only synchronous API with pageable host staging. No retained pinned
     * buffers or async launch is supported by this backend yet. */
    return (uint32_t)cudaMemcpy(dst, src, bytes, kind);
}
static uint32_t synchronize(void *context)
{
    if (!owned(context)) return (uint32_t)cudaErrorInvalidResourceHandle;
    return (uint32_t)cudaDeviceSynchronize();
}
static const struct flyt_cuda_backend backend = {
    .get_count = get_count, .get_device = get_device, .set_device = set_device,
    .allocate = allocate, .release = release, .copy = copy,
    .synchronize = synchronize
};

int flyt_cuda_runtime_open(struct flyt_cuda_runtime **out, uint32_t *cuda_error)
{
    struct flyt_cuda_runtime *r;
    int32_t count = 0;
    if (!out || !cuda_error) return FLYT_SHM_BAD_DESCRIPTOR;
    *out = NULL;
    *cuda_error = 0;
    r = calloc(1, sizeof(*r));
    if (!r) return FLYT_SHM_INTERNAL_ERROR;
    if (atomic_flag_test_and_set(&runtime_opened)) {
        free(r);
        return FLYT_SHM_CHANNEL_CLOSED;
    }
    r->owner = pthread_self();
    if(worker_guard()){free(r);return FLYT_SHM_INTERNAL_ERROR;}
    *cuda_error = get_count(r, &count);
    if (!*cuda_error) *cuda_error = set_device(r, 0);
    if (*cuda_error) {
        free(r);
        return FLYT_SHM_INTERNAL_ERROR;
    }
    *out = r;
    return FLYT_SHM_OK;
}
const struct flyt_cuda_backend *flyt_cuda_runtime_backend(void) { return &backend; }
void flyt_cuda_runtime_close(struct flyt_cuda_runtime *r)
{
    if (owned(r)) free(r);
    /* Never reset the physical GPU or allow this process to serve a new client. */
}
