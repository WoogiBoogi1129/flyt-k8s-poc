#define _GNU_SOURCE
#include "flyt_guest.h"
#include <cuda_runtime_api.h>
#include <cuda.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>
#include <stdatomic.h>

pthread_mutex_t flyt_guest_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t io_lock=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t io_cond=PTHREAD_COND_INITIALIZER;
static pthread_t io_thread;
static int started,initialized,work,finished;
static atomic_int broken;
static pid_t owner_pid;
static struct {uint32_t api;const void *input;size_t bytes;void *output;size_t capacity,received;int result;} job;
static struct {void *base;size_t bytes,mapped;uint64_t handle;} allocations[4096];
static _Thread_local cudaError_t last_error;
static void before_fork(void){pthread_mutex_lock(&flyt_guest_lock);pthread_mutex_lock(&io_lock);}
static void after_fork(void){pthread_mutex_unlock(&io_lock);pthread_mutex_unlock(&flyt_guest_lock);}
static void child_fork(void){broken=1;after_fork();}
__attribute__((constructor)) static void setup_fork(void){pthread_atfork(before_fork,after_fork,child_fork);}

static int exchange(struct flyt_shm_channel *c,struct flyt_shm_identity identity,uint64_t *id,
                    uint32_t api,const void *in,size_t n,void *out,size_t cap,size_t *got){
    struct flyt_shm_request q={.identity=identity,.request_id=*id,.api_id=api,.payload_schema=1,.input=in,.input_bytes=n};
    struct flyt_shm_response r={.output=out,.output_capacity=cap};
    if(flyt_shm_submit(c,&q)||flyt_shm_receive(c,*id,&r)){broken=1;return 999;}
    ++*id;*got=r.output_bytes;
    if(r.transport_status){if(r.transport_status==FLYT_SHM_UNSUPPORTED_API)return 801;broken=1;return 999;}
    if(r.result_domain!=FLYT_RESULT_CUDA_RUNTIME&&r.result_domain!=FLYT_RESULT_CUDA_DRIVER&&r.result_domain!=FLYT_RESULT_LIBRARY&&api>FLYT_HEARTBEAT){broken=1;return 999;}
    return (int)r.api_result;
}
static void *io_main(void *unused){
    (void)unused;struct flyt_shm_layout l;struct flyt_mapping m={0};struct flyt_shm_channel *c=NULL;
    struct flyt_shm_identity identity;uint64_t id=1;size_t got=0;unsigned long slot=0;char *end;
    const char *path=getenv("FLYT_LAYOUT"),*bdf=getenv("FLYT_IVSHMEM_BDF"),*slotenv=getenv("FLYT_SLOT");
    if(slotenv){slot=strtoul(slotenv,&end,10);if(*end||slot>=32)broken=1;}
    if(!path||!bdf||broken||flyt_layout_read(path,&l)||slot>=l.session_count||flyt_map_guest(bdf,&l,&m)||
       flyt_shm_open(m.address,m.bytes,&l,(uint32_t)slot,FLYT_SHM_GUEST,30000,&c))broken=1;
    if(!broken){memcpy(identity.channel_generation,l.channel_generation,16);memcpy(identity.session_id,l.slots[slot].session_id,16);
        if(exchange(c,identity,&id,FLYT_HELLO,NULL,0,NULL,0,&got))broken=1;}
    pthread_mutex_lock(&io_lock);initialized=1;pthread_cond_broadcast(&io_cond);
    while(!broken){
        while(!work)pthread_cond_wait(&io_cond,&io_lock);
        pthread_mutex_unlock(&io_lock);
        job.result=exchange(c,identity,&id,job.api,job.input,job.bytes,job.output,job.capacity,&job.received);
        pthread_mutex_lock(&io_lock);work=0;finished=1;pthread_cond_broadcast(&io_cond);
    }
    pthread_cond_broadcast(&io_cond);pthread_mutex_unlock(&io_lock);flyt_shm_close(c);flyt_unmap(&m);return NULL;
}
int flyt_guest_exchange(uint32_t api,const void *input,size_t bytes,void *output,size_t capacity,size_t *received){
    if(owner_pid&&owner_pid!=getpid())return 999;
    pthread_mutex_lock(&io_lock);
    if(!started){owner_pid=getpid();started=1;if(pthread_create(&io_thread,NULL,io_main,NULL)){broken=1;initialized=1;}}
    while(!initialized)pthread_cond_wait(&io_cond,&io_lock);
    if(broken){pthread_mutex_unlock(&io_lock);return 999;}
    job.api=api;job.input=input;job.bytes=bytes;job.output=output;job.capacity=capacity;job.received=0;
    finished=0;work=1;pthread_cond_broadcast(&io_cond);
    while(!finished&&!broken)pthread_cond_wait(&io_cond,&io_lock);
    int result=finished?job.result:999;if(received)*received=job.received;
    pthread_mutex_unlock(&io_lock);return result;
}
int flyt_guest_ref(const void *p,size_t n,struct flyt_device_ref *r){
    uintptr_t v=(uintptr_t)p;
    for(unsigned i=0;i<4096;i++)if(allocations[i].base){uintptr_t b=(uintptr_t)allocations[i].base;
        if(v>=b&&v-b<=allocations[i].bytes&&n<=allocations[i].bytes-(v-b)){r->handle=allocations[i].handle;r->offset=v-b;return 0;}}
    return 1;
}
static cudaError_t finish(int e){last_error=(cudaError_t)e;return last_error;}
cudaError_t cudaMalloc(void **out,size_t n){
    if(!out)return finish(1);*out=NULL;pthread_mutex_lock(&flyt_guest_lock);unsigned i;
    for(i=0;i<4096&&allocations[i].base;i++);int e=2;uint8_t in[8],result[8];size_t got=0;
    if(i==4096||n>SIZE_MAX-4095)goto done;
    flyt_put(in,n,8);e=flyt_guest_exchange(FLYT_API_RUNTIME_MALLOC,in,8,result,8,&got);
    if(e)goto done;if(got!=8){e=999;goto done;}uint64_t handle=flyt_get(result,8);if(!n&&!handle)goto done;
    size_t mapped=(n+4095)&~(size_t)4095;if(!mapped){e=999;goto done;}
    void *p=mmap(NULL,mapped,PROT_NONE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    if(p==MAP_FAILED){flyt_put(in,handle,8);flyt_guest_exchange(FLYT_API_RUNTIME_FREE,in,8,NULL,0,&got);e=2;goto done;}
    allocations[i].base=p;allocations[i].bytes=n;allocations[i].mapped=mapped;allocations[i].handle=handle;*out=p;
done:pthread_mutex_unlock(&flyt_guest_lock);return finish(e);
}
cudaError_t cudaFree(void *p){
    pthread_mutex_lock(&flyt_guest_lock);unsigned i;uint64_t handle=0;int e=0;size_t got;uint8_t in[8];
    for(i=0;i<4096;i++)if(allocations[i].base==p&&p){handle=allocations[i].handle;break;}
    if(p&&!handle)e=1;
    else{flyt_put(in,handle,8);e=flyt_guest_exchange(FLYT_API_RUNTIME_FREE,in,8,NULL,0,&got);
        if(!e&&p){munmap(p,allocations[i].mapped);memset(&allocations[i],0,sizeof(allocations[i]));}}
    pthread_mutex_unlock(&flyt_guest_lock);return finish(e);
}
cudaError_t cudaMemcpy(void *dst,const void *src,size_t n,enum cudaMemcpyKind kind){
    pthread_mutex_lock(&flyt_guest_lock);int e=0;struct flyt_device_ref d={0},s={0};size_t got=0;
    if(n>FLYT_EXEC_MAX_COPY_BYTES){e=1;goto done;}
    if(kind==cudaMemcpyDefault){int dh=!flyt_guest_ref(dst,n,&d),sh=!flyt_guest_ref(src,n,&s);
        kind=dh?(sh?cudaMemcpyDeviceToDevice:cudaMemcpyHostToDevice):(sh?cudaMemcpyDeviceToHost:cudaMemcpyHostToHost);}
    if(kind==cudaMemcpyHostToHost){if(n&&(!dst||!src)){e=1;goto done;}if(n)memmove(dst,src,n);goto done;}
    if(kind!=cudaMemcpyHostToDevice&&kind!=cudaMemcpyDeviceToHost&&kind!=cudaMemcpyDeviceToDevice){e=1;goto done;}
    if(kind!=cudaMemcpyDeviceToHost&&flyt_guest_ref(dst,n,&d)){e=1;goto done;}
    if(kind!=cudaMemcpyHostToDevice&&flyt_guest_ref(src,n,&s)){e=1;goto done;}
    if(n&&((kind==cudaMemcpyHostToDevice&&!src)||(kind==cudaMemcpyDeviceToHost&&!dst))){e=1;goto done;}
    size_t bytes=48+(kind==cudaMemcpyHostToDevice?n:0);uint8_t *in=calloc(1,bytes);if(!in){e=2;goto done;}
    flyt_put(in,kind==cudaMemcpyHostToDevice?1:kind==cudaMemcpyDeviceToHost?2:3,4);
    flyt_put(in+8,d.handle,8);flyt_put(in+16,d.offset,8);flyt_put(in+24,s.handle,8);flyt_put(in+32,s.offset,8);flyt_put(in+40,n,8);
    if(kind==cudaMemcpyHostToDevice&&n)memcpy(in+48,src,n);
    e=flyt_guest_exchange(FLYT_API_RUNTIME_MEMCPY,in,bytes,kind==cudaMemcpyDeviceToHost?dst:NULL,kind==cudaMemcpyDeviceToHost?n:0,&got);
    if(!e&&got!=(kind==cudaMemcpyDeviceToHost?n:0))e=999;free(in);
done:pthread_mutex_unlock(&flyt_guest_lock);return finish(e);
}
static cudaError_t scalar(uint32_t api,int *out,int input){
    uint8_t in[4],result[4];size_t got;flyt_put(in,(uint32_t)input,4);
    pthread_mutex_lock(&flyt_guest_lock);int e=flyt_guest_exchange(api,api==FLYT_API_RUNTIME_SET_DEVICE?in:NULL,api==FLYT_API_RUNTIME_SET_DEVICE?4:0,result,4,&got);
    if(!e&&out){if(got!=4)e=999;else *out=(int)flyt_get(result,4);}pthread_mutex_unlock(&flyt_guest_lock);return finish(e);
}
cudaError_t cudaGetDeviceCount(int *n){return n?scalar(FLYT_API_RUNTIME_GET_DEVICE_COUNT,n,0):finish(1);}
cudaError_t cudaGetDevice(int *n){return n?scalar(FLYT_API_RUNTIME_GET_DEVICE,n,0):finish(1);}
cudaError_t cudaSetDevice(int n){return scalar(FLYT_API_RUNTIME_SET_DEVICE,NULL,n);}
cudaError_t cudaDeviceSynchronize(void){return scalar(FLYT_API_RUNTIME_DEVICE_SYNCHRONIZE,NULL,0);}
cudaError_t cudaGetLastError(void){cudaError_t e=last_error;last_error=cudaSuccess;return e;}
cudaError_t cudaPeekAtLastError(void){return last_error;}
static CUresult driver(cudaError_t e){return e==cudaSuccess?CUDA_SUCCESS:e==cudaErrorInvalidValue?CUDA_ERROR_INVALID_VALUE:e==cudaErrorMemoryAllocation?CUDA_ERROR_OUT_OF_MEMORY:e==cudaErrorNotSupported?CUDA_ERROR_NOT_SUPPORTED:CUDA_ERROR_UNKNOWN;}
CUresult cuInit(unsigned flags){int n;if(flags)return CUDA_ERROR_INVALID_VALUE;return driver(cudaGetDeviceCount(&n));}
CUresult cuDeviceGetCount(int *n){return driver(cudaGetDeviceCount(n));}
CUresult cuDeviceGet(CUdevice *d,int ordinal){if(!d||ordinal)return CUDA_ERROR_INVALID_DEVICE;int n;CUresult e=driver(cudaGetDeviceCount(&n));if(e==CUDA_SUCCESS)*d=0;return e;}
CUresult cuMemAlloc_v2(CUdeviceptr *p,size_t n){if(!p)return CUDA_ERROR_INVALID_VALUE;void *v=NULL;cudaError_t e=cudaMalloc(&v,n);*p=(CUdeviceptr)(uintptr_t)v;return driver(e);}
CUresult cuMemFree_v2(CUdeviceptr p){return driver(cudaFree((void *)(uintptr_t)p));}
CUresult cuMemcpyHtoD_v2(CUdeviceptr d,const void *s,size_t n){return driver(cudaMemcpy((void *)(uintptr_t)d,s,n,cudaMemcpyHostToDevice));}
CUresult cuMemcpyDtoH_v2(void *d,CUdeviceptr s,size_t n){return driver(cudaMemcpy(d,(void *)(uintptr_t)s,n,cudaMemcpyDeviceToHost));}
CUresult cuMemcpyDtoD_v2(CUdeviceptr d,CUdeviceptr s,size_t n){return driver(cudaMemcpy((void *)(uintptr_t)d,(void *)(uintptr_t)s,n,cudaMemcpyDeviceToDevice));}
CUresult cuCtxSynchronize(void){return driver(cudaDeviceSynchronize());}
