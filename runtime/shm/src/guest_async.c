#include "flyt_guest.h"
#include "flyt_async.h"
#include <cuda_runtime_api.h>
#include <cuda.h>
#include <stdlib.h>
#include <string.h>

static int handle_call(uint32_t api,uint64_t id,uint64_t other,void **out){
    uint8_t in[16],result[8];size_t got=0;flyt_put(in,id,8);flyt_put(in+8,other,8);
    size_t n=(api==FLYT_STREAM_CREATE||api==FLYT_EVENT_CREATE)?4:api==FLYT_EVENT_RECORD?16:8;
    pthread_mutex_lock(&flyt_guest_lock);int e=flyt_guest_exchange(api,in,n,result,8,&got);
    if(!e&&out){if(got!=8)e=999;else *out=(void *)(uintptr_t)flyt_get(result,8);}
    pthread_mutex_unlock(&flyt_guest_lock);return e;
}
cudaError_t cudaStreamCreateWithFlags(cudaStream_t *s,unsigned f){return s?(cudaError_t)handle_call(FLYT_STREAM_CREATE,f,0,(void **)s):cudaErrorInvalidValue;}
cudaError_t cudaStreamCreate(cudaStream_t *s){return cudaStreamCreateWithFlags(s,0);}
cudaError_t cudaStreamDestroy(cudaStream_t s){return (cudaError_t)handle_call(FLYT_STREAM_DESTROY,(uintptr_t)s,0,NULL);}
cudaError_t cudaStreamSynchronize(cudaStream_t s){return (cudaError_t)handle_call(FLYT_STREAM_SYNC,(uintptr_t)s,0,NULL);}
cudaError_t cudaStreamQuery(cudaStream_t s){return (cudaError_t)handle_call(FLYT_STREAM_QUERY,(uintptr_t)s,0,NULL);}
cudaError_t cudaEventCreateWithFlags(cudaEvent_t *e,unsigned f){return e?(cudaError_t)handle_call(FLYT_EVENT_CREATE,f,0,(void **)e):cudaErrorInvalidValue;}
cudaError_t cudaEventCreate(cudaEvent_t *e){return cudaEventCreateWithFlags(e,0);}
cudaError_t cudaEventDestroy(cudaEvent_t e){return (cudaError_t)handle_call(FLYT_EVENT_DESTROY,(uintptr_t)e,0,NULL);}
cudaError_t cudaEventRecord(cudaEvent_t e,cudaStream_t s){return (cudaError_t)handle_call(FLYT_EVENT_RECORD,(uintptr_t)e,(uintptr_t)s,NULL);}
cudaError_t cudaEventSynchronize(cudaEvent_t e){return (cudaError_t)handle_call(FLYT_EVENT_SYNC,(uintptr_t)e,0,NULL);}
cudaError_t cudaEventQuery(cudaEvent_t e){return (cudaError_t)handle_call(FLYT_EVENT_QUERY,(uintptr_t)e,0,NULL);}
cudaError_t cudaMemcpyAsync(void *dst,const void *src,size_t n,enum cudaMemcpyKind kind,cudaStream_t st){
    if(n>FLYT_EXEC_MAX_COPY_BYTES)return cudaErrorInvalidValue;
    pthread_mutex_lock(&flyt_guest_lock);struct flyt_device_ref d={0},s={0};int e=1;size_t got;
    if(kind==cudaMemcpyDefault){int dh=!flyt_guest_ref(dst,n,&d),sh=!flyt_guest_ref(src,n,&s);kind=dh?(sh?cudaMemcpyDeviceToDevice:cudaMemcpyHostToDevice):(sh?cudaMemcpyDeviceToHost:cudaMemcpyHostToHost);}
    if(kind==cudaMemcpyHostToHost){if(n&&(!dst||!src))goto done;if(n)memmove(dst,src,n);e=0;goto done;}
    if(kind<cudaMemcpyHostToDevice||kind>cudaMemcpyDeviceToDevice)goto done;
    if(kind!=cudaMemcpyDeviceToHost&&flyt_guest_ref(dst,n,&d))goto done;
    if(kind!=cudaMemcpyHostToDevice&&flyt_guest_ref(src,n,&s))goto done;
    if(n&&((kind==cudaMemcpyHostToDevice&&!src)||(kind==cudaMemcpyDeviceToHost&&!dst)))goto done;
    size_t bytes=56+(kind==cudaMemcpyHostToDevice?n:0);uint8_t *in=calloc(1,bytes);if(!in){e=2;goto done;}
    flyt_put(in,(uintptr_t)st,8);flyt_put(in+8,kind==cudaMemcpyHostToDevice?1:kind==cudaMemcpyDeviceToHost?2:3,4);
    flyt_put(in+16,d.handle,8);flyt_put(in+24,d.offset,8);flyt_put(in+32,s.handle,8);flyt_put(in+40,s.offset,8);flyt_put(in+48,n,8);
    if(kind==cudaMemcpyHostToDevice&&n)memcpy(in+56,src,n);
    e=flyt_guest_exchange(FLYT_ASYNC_COPY,in,bytes,kind==cudaMemcpyDeviceToHost?dst:NULL,kind==cudaMemcpyDeviceToHost?n:0,&got);
    if(!e&&got!=(kind==cudaMemcpyDeviceToHost?n:0))e=999;free(in);
done:pthread_mutex_unlock(&flyt_guest_lock);return (cudaError_t)e;
}
CUresult cuModuleLoadData(CUmodule *m,const void *image){
    if(!m||!image)return CUDA_ERROR_INVALID_VALUE;
    /* Text PTX only. Never guess ELF/fatbin extent from an unbounded pointer. */
    const char *text=image;if(text[0]!='/'&&text[0]!='.'&&text[0]!='\n')return CUDA_ERROR_NOT_SUPPORTED;
    size_t n=strnlen(text,FLYT_EXEC_MAX_COPY_BYTES);if(n==FLYT_EXEC_MAX_COPY_BYTES)return CUDA_ERROR_INVALID_VALUE;
    uint8_t out[8];size_t got;pthread_mutex_lock(&flyt_guest_lock);
    int e=flyt_guest_exchange(FLYT_MODULE_LOAD,image,n+1,out,8,&got);
    if(!e){if(got!=8)e=CUDA_ERROR_UNKNOWN;else *m=(CUmodule)(uintptr_t)flyt_get(out,8);}
    pthread_mutex_unlock(&flyt_guest_lock);return (CUresult)e;
}
CUresult cuModuleUnload(CUmodule m){return (CUresult)handle_call(FLYT_MODULE_UNLOAD,(uintptr_t)m,0,NULL);}
CUresult cuModuleGetFunction(CUfunction *f,CUmodule m,const char *name){
    if(!f||!name)return CUDA_ERROR_INVALID_VALUE;size_t n=strnlen(name,256);if(!n||n==256)return CUDA_ERROR_INVALID_VALUE;
    uint8_t in[264],out[8];size_t got;flyt_put(in,(uintptr_t)m,8);memcpy(in+8,name,n+1);
    pthread_mutex_lock(&flyt_guest_lock);int e=flyt_guest_exchange(FLYT_FUNCTION_GET,in,n+9,out,8,&got);
    if(!e){if(got!=8)e=CUDA_ERROR_UNKNOWN;else *f=(CUfunction)(uintptr_t)flyt_get(out,8);}
    pthread_mutex_unlock(&flyt_guest_lock);return (CUresult)e;
}
/* Explicit ABI metadata avoids guessing whether a 64-bit scalar is a pointer.
 * Call this extension after cuModuleGetFunction. Each parameter is 1..16 bytes;
 * device-pointer parameters must be 8 bytes. No automatic PyTorch registration.
 */
static struct {CUfunction function;unsigned count;unsigned char sizes[64],pointers[64];} signatures[4096];
int flytRegisterKernelABI(CUfunction f,unsigned count,const unsigned char *sizes,const unsigned char *pointers){
    if(!f||count>64||(count&&(!sizes||!pointers)))return 1;
    for(unsigned j=0;j<count;j++)if(!sizes[j]||sizes[j]>16||pointers[j]>1||(pointers[j]&&sizes[j]!=8))return 1;
    pthread_mutex_lock(&flyt_guest_lock);unsigned i;for(i=0;i<4096;i++)if(!signatures[i].function||signatures[i].function==f)break;
    if(i<4096){signatures[i].function=f;signatures[i].count=count;if(count){memcpy(signatures[i].sizes,sizes,count);memcpy(signatures[i].pointers,pointers,count);}}
    pthread_mutex_unlock(&flyt_guest_lock);return i<4096?0:1;
}
CUresult cuLaunchKernel(CUfunction f,unsigned gx,unsigned gy,unsigned gz,unsigned bx,unsigned by,unsigned bz,unsigned shared,CUstream st,void **params,void **extra){
    if(extra)return CUDA_ERROR_NOT_SUPPORTED;pthread_mutex_lock(&flyt_guest_lock);
    unsigned i;int e=CUDA_ERROR_NOT_SUPPORTED;for(i=0;i<4096;i++)if(signatures[i].function==f)break;
    if(i==4096)goto done;unsigned count=signatures[i].count;if(count&&!params){e=CUDA_ERROR_INVALID_VALUE;goto done;}
    uint8_t in[48+24*64]={0};size_t got;flyt_put(in,(uintptr_t)f,8);flyt_put(in+8,(uintptr_t)st,8);
    unsigned dims[]={gx,gy,gz,bx,by,bz,shared,count};for(unsigned j=0;j<8;j++)flyt_put(in+16+4*j,dims[j],4);
    for(unsigned j=0;j<count;j++){
        if(!params[j]){e=CUDA_ERROR_INVALID_VALUE;goto done;}uint8_t *p=in+48+24*j;unsigned size=signatures[i].sizes[j];
        flyt_put(p,signatures[i].pointers[j],4);flyt_put(p+4,size,4);
        if(signatures[i].pointers[j]){void *ptr;struct flyt_device_ref r;memcpy(&ptr,params[j],8);
            if(flyt_guest_ref(ptr,0,&r)){e=CUDA_ERROR_INVALID_VALUE;goto done;}flyt_put(p+8,r.handle,8);flyt_put(p+16,r.offset,8);}
        else memcpy(p+8,params[j],size);
    }
    e=flyt_guest_exchange(FLYT_KERNEL_LAUNCH,in,48+24*count,NULL,0,&got);
done:pthread_mutex_unlock(&flyt_guest_lock);return (CUresult)e;
}
