#define _GNU_SOURCE
#include "flyt_guest.h"
#include "flyt_torch.h"
#include "flyt_async.h"
#include <cuda_runtime_api.h>
#include <cuda.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

static int exchange(uint32_t api,const void *p,size_t n,void *out,size_t capacity,size_t *got){
    pthread_mutex_lock(&flyt_guest_lock);
    int e=flyt_guest_exchange(api,p,n,out,capacity,got);
    pthread_mutex_unlock(&flyt_guest_lock);return e;
}
static cudaError_t integer(uint32_t api,int input,int *out){
    if(!out)return cudaErrorInvalidValue;uint8_t p[4],r[4];size_t got=0;flyt_put(p,(uint32_t)input,4);
    int e=exchange(api,p,4,r,4,&got);if(!e){if(got!=4)e=999;else *out=(int)flyt_get(r,4);}return (cudaError_t)e;
}
cudaError_t cudaGetDeviceProperties(struct cudaDeviceProp *prop,int device){
    if(!prop||device)return cudaErrorInvalidValue;uint8_t p[4],r[FLYT_PROPERTY_CAPACITY];size_t got=0,at=0;
    flyt_put(p,12080,4);int e=exchange(FLYT_DEVICE_PROPERTIES,p,4,r,sizeof(r),&got);if(e)return (cudaError_t)e;
    memset(prop,0,sizeof(*prop));
#define FLYT_PROP_NUMBER(field,width) if(got-at<width)return cudaErrorUnknown;prop->field=flyt_get(r+at,width);at+=width;
#define FLYT_PROP_BYTES(field,bytes) if(got-at<bytes)return cudaErrorUnknown;memcpy(prop->field,r+at,bytes);at+=bytes;
#include "flyt_properties.inc"
#undef FLYT_PROP_NUMBER
#undef FLYT_PROP_BYTES
    return got==at?cudaSuccess:cudaErrorUnknown;
}
cudaError_t cudaDeviceGetAttribute(int *out,enum cudaDeviceAttr attr,int device){return device?cudaErrorInvalidDevice:integer(FLYT_DEVICE_ATTRIBUTE,attr,out);}
cudaError_t cudaDriverGetVersion(int *out){return integer(FLYT_CUDA_VERSION,1,out);}
cudaError_t cudaRuntimeGetVersion(int *out){return integer(FLYT_CUDA_VERSION,0,out);}
cudaError_t cudaDeviceGetStreamPriorityRange(int *least,int *greatest){
    if(!least||!greatest)return cudaErrorInvalidValue;uint8_t r[8];size_t got;
    int e=exchange(FLYT_PRIORITY_RANGE,NULL,0,r,8,&got);
    if(!e){if(got!=8)e=999;else{*least=(int)flyt_get(r,4);*greatest=(int)flyt_get(r+4,4);}}return (cudaError_t)e;
}
cudaError_t cudaStreamCreateWithPriority(cudaStream_t *stream,unsigned flags,int priority){
    if(!stream)return cudaErrorInvalidValue;uint8_t p[8],r[8];size_t got;
    flyt_put(p,flags,4);flyt_put(p+4,(uint32_t)priority,4);
    int e=exchange(FLYT_STREAM_PRIORITY_CREATE,p,8,r,8,&got);
    if(!e){if(got!=8)e=999;else *stream=(cudaStream_t)(uintptr_t)flyt_get(r,8);}return (cudaError_t)e;
}
cudaError_t cudaStreamIsCapturing(cudaStream_t stream,enum cudaStreamCaptureStatus *status){
    /* All capture entry APIs are rejected in this eager-only runtime. Validate
     * a non-default stream remotely before reporting its non-capture state. */
    if(!status)return cudaErrorInvalidValue;
    cudaError_t e=cudaStreamQuery(stream);if(e!=cudaSuccess&&e!=cudaErrorNotReady)return e;
    *status=cudaStreamCaptureStatusNone;return cudaSuccess;
}
cudaError_t cudaMemsetAsync(void *dst,int value,size_t bytes,cudaStream_t stream){
    uint8_t p[40]={0};size_t got;struct flyt_device_ref ref;
    pthread_mutex_lock(&flyt_guest_lock);int e=cudaErrorInvalidValue;
    if(!flyt_guest_ref(dst,bytes,&ref)){
        flyt_put(p,ref.handle,8);flyt_put(p+8,ref.offset,8);flyt_put(p+16,bytes,8);
        flyt_put(p+24,(uintptr_t)stream,8);flyt_put(p+32,(uint32_t)value,4);
        e=flyt_guest_exchange(FLYT_MEMSET,p,40,NULL,0,&got);
    }
    pthread_mutex_unlock(&flyt_guest_lock);return (cudaError_t)e;
}
cudaError_t cudaMemset(void *dst,int value,size_t bytes){cudaError_t e=cudaMemsetAsync(dst,value,bytes,0);return e?e:cudaDeviceSynchronize();}
const char *cudaGetErrorString(cudaError_t e){
    switch(e){case cudaSuccess:return "success";case cudaErrorInvalidValue:return "invalid value";
    case cudaErrorMemoryAllocation:return "memory allocation failed (GPU quota or Guest VA reservation)";
    case cudaErrorNotSupported:return "unsupported SHM CUDA operation";case cudaErrorNotReady:return "not ready";
    default:return "SHM CUDA error; inspect Worker and Guest logs";}
}
const char *cudaGetErrorName(cudaError_t e){
    switch(e){case cudaSuccess:return "cudaSuccess";case cudaErrorInvalidValue:return "cudaErrorInvalidValue";
    case cudaErrorMemoryAllocation:return "cudaErrorMemoryAllocation";case cudaErrorNotSupported:return "cudaErrorNotSupported";
    case cudaErrorNotReady:return "cudaErrorNotReady";default:return "cudaErrorUnknown";}
}
CUresult cuDevicePrimaryCtxGetState(CUdevice device,unsigned *flags,int *active){
    if(device||!flags||!active)return CUDA_ERROR_INVALID_VALUE;uint8_t out[8];size_t got;
    int e=exchange(FLYT_PRIMARY_CONTEXT_STATE,NULL,0,out,8,&got);
    if(!e){if(got!=8)e=999;else{*flags=(unsigned)flyt_get(out,4);*active=(int)flyt_get(out+4,4);}}return (CUresult)e;
}
CUresult cuCtxGetCurrent(CUcontext *out){
    if(!out)return CUDA_ERROR_INVALID_VALUE;unsigned flags=0;int active=0;
    CUresult e=cuDevicePrimaryCtxGetState(0,&flags,&active);
    *out=e||!active?NULL:(CUcontext)(uintptr_t)1;return e;
}
CUresult cuCtxSetCurrent(CUcontext context){
    if(context!=(CUcontext)(uintptr_t)1)return CUDA_ERROR_NOT_SUPPORTED;
    CUcontext actual=NULL;CUresult e=cuCtxGetCurrent(&actual);return e?e:actual==context?CUDA_SUCCESS:CUDA_ERROR_INVALID_CONTEXT;
}
CUresult cuCtxGetDevice(CUdevice *device){return (CUresult)cudaGetDevice(device);}
CUresult cuDeviceGetAttribute(int *out,CUdevice_attribute attr,CUdevice device){return (CUresult)cudaDeviceGetAttribute(out,(enum cudaDeviceAttr)attr,device);}
CUresult cuGetErrorString(CUresult error,const char **out){if(!out)return CUDA_ERROR_INVALID_VALUE;*out=cudaGetErrorString((cudaError_t)error);return CUDA_SUCCESS;}
#undef cuGetProcAddress
extern CUresult cuGetProcAddress(const char *,void **,int,cuuint64_t);
cudaError_t cudaGetDriverEntryPointByVersion(const char *name,void **out,unsigned version,unsigned long long flags,enum cudaDriverEntryPointQueryResult *status){
    if(!name||!out)return cudaErrorInvalidValue;*out=NULL;
    CUresult e=cuGetProcAddress(name,out,(int)version,flags);
    if(status)*status=e==CUDA_SUCCESS?cudaDriverEntryPointSuccess:cudaDriverEntryPointSymbolNotFound;
    if(e)fprintf(stderr,"flyt unsupported driver entry: %s version=%u flags=%llu\n",name,version,flags);
    return e==CUDA_SUCCESS?cudaSuccess:cudaErrorNotSupported;
}
cudaError_t cudaGetDriverEntryPoint(const char *name,void **out,unsigned long long flags,enum cudaDriverEntryPointQueryResult *status){
    return cudaGetDriverEntryPointByVersion(name,out,12080,flags,status);
}
cudaError_t cudaPointerGetAttributes(struct cudaPointerAttributes *out,const void *ptr){
    if(!out)return cudaErrorInvalidValue;struct flyt_device_ref ref;
    memset(out,0,sizeof(*out));pthread_mutex_lock(&flyt_guest_lock);
    int device=!flyt_guest_ref(ptr,0,&ref);pthread_mutex_unlock(&flyt_guest_lock);
    out->type=device?cudaMemoryTypeDevice:cudaMemoryTypeUnregistered;out->device=0;
    if(device)out->devicePointer=(void*)ptr;else out->hostPointer=(void*)ptr;return cudaSuccess;
}

/* Registration is local and lazy: loading libtorch must not open a channel or
 * initialize a native GPU in Guest. Each fatbin is uploaded once on first use. */
struct module { const uint8_t *data;size_t bytes;uint64_t remote;int valid;struct module *next; };
struct function { const void *host;char *name;struct module *module;uint64_t remote;
    unsigned count;uint32_t sizes[64];struct function *next; };
static struct module *modules;static struct function *functions;
static pthread_mutex_t registration_lock=PTHREAD_MUTEX_INITIALIZER;
void __cudaRegisterVar(void **handle,char *host,char *device,const char *name,int ext,size_t bytes,int constant,int global){
    /* Static device data is included in the Worker-loaded fatbinary. Host-side
     * symbol-address access is explicitly unsupported below. */
    (void)handle;(void)host;(void)device;(void)name;(void)ext;(void)bytes;(void)constant;(void)global;
}
cudaError_t cudaGetSymbolAddress(void **out,const void *symbol){(void)symbol;if(out)*out=NULL;return cudaErrorNotSupported;}
void **__cudaRegisterFatBinary(void *wrapper){
    if(!wrapper)return NULL;const uint8_t *w=wrapper;const uint8_t *data=NULL;
    if(flyt_get(w,4)!=0x466243b1||flyt_get(w+4,4)!=1)return NULL;
    memcpy(&data,w+8,sizeof(data));if(!data||flyt_get(data,4)!=0xba55ed50)return NULL;
    size_t header=(size_t)flyt_get(data+6,2);uint64_t payload=flyt_get(data+8,8);
    if(header<16||header>4096||payload>FLYT_MAX_MODULE_BYTES-header)return NULL;
    struct module *m=calloc(1,sizeof(*m));if(!m)return NULL;
    m->data=data;m->bytes=header+(size_t)payload;m->valid=1;
    pthread_mutex_lock(&registration_lock);m->next=modules;modules=m;pthread_mutex_unlock(&registration_lock);
    return (void**)m;
}
void __cudaRegisterFatBinaryEnd(void **handle){(void)handle;}
void __cudaUnregisterFatBinary(void **handle){
    pthread_mutex_lock(&registration_lock);
    for(struct module *m=modules;m;m=m->next)if((void**)m==handle){m->valid=0;break;}
    /* Worker-owned CUDA modules are released at session close. Keep host
     * registration records to reject stale function pointers deterministically. */
    pthread_mutex_unlock(&registration_lock);
}
void __cudaRegisterFunction(void **handle,const char *host,char *device,const char *name,
                          int limit,void *tid,void *bid,void *bd,void *gd,int *ws){
    (void)device;(void)limit;(void)tid;(void)bid;(void)bd;(void)gd;(void)ws;
    if(!name||strnlen(name,4096)==4096)return;
    pthread_mutex_lock(&registration_lock);struct module *m=modules;
    while(m&&(void**)m!=handle)m=m->next;
    if(m&&m->valid){struct function *f=calloc(1,sizeof(*f));if(f){f->name=strdup(name);
        if(f->name){f->host=host;f->module=m;f->next=functions;functions=f;}else free(f);}}
    pthread_mutex_unlock(&registration_lock);
}
static int prepare(struct function *f){
    struct module *m=f->module;uint8_t p[16],out[8+64*4];size_t got=0;int e;
    if(!m->valid)return 801;
    if(!m->remote){
        flyt_put(p,m->bytes,8);e=exchange(FLYT_MODULE_BEGIN,p,8,out,8,&got);if(e)return e;
        if(got!=8)return 999;uint64_t upload=flyt_get(out,8);
        uint8_t *chunk=malloc(65536+16);if(!chunk)return 2;
        for(size_t pos=0;pos<m->bytes;){size_t n=m->bytes-pos;if(n>65536)n=65536;
            flyt_put(chunk,upload,8);flyt_put(chunk+8,pos,8);memcpy(chunk+16,m->data+pos,n);
            e=exchange(FLYT_MODULE_CHUNK,chunk,n+16,NULL,0,&got);if(e){free(chunk);return e;}pos+=n;}
        free(chunk);flyt_put(p,upload,8);e=exchange(FLYT_MODULE_COMMIT,p,8,out,8,&got);if(e)return e;
        if(got!=8)return 999;m->remote=flyt_get(out,8);
    }
    if(!f->remote){
        size_t bytes=strlen(f->name)+1;uint8_t *name=malloc(bytes+8);if(!name)return 2;
        flyt_put(name,m->remote,8);memcpy(name+8,f->name,bytes);
        e=exchange(FLYT_FUNCTION_GET,name,bytes+8,out,8,&got);free(name);if(e)return e;
        if(got!=8)return 999;uint64_t function=flyt_get(out,8);flyt_put(p,function,8);
        e=exchange(FLYT_FUNCTION_LAYOUT,p,8,out,sizeof(out),&got);if(e)return e;
        if(got<8)return 999;
        unsigned count=(unsigned)flyt_get(out,4);if(count>64||got!=8+4*count||flyt_get(out+4,4))return 999;
        for(unsigned i=0;i<count;i++)f->sizes[i]=(uint32_t)flyt_get(out+8+4*i,4);
        f->count=count;f->remote=function;
    }
    return 0;
}
cudaError_t cudaLaunchKernel(const void *host,struct dim3 grid,struct dim3 block,void **args,size_t shared,cudaStream_t stream){
    if(!flyt_guest_mirror_enabled())return cudaErrorNotSupported;
    if(shared>UINT32_MAX)return cudaErrorInvalidValue;
    pthread_mutex_lock(&registration_lock);struct function *f=functions;
    while(f&&f->host!=host)f=f->next;
    int e=801;if(!f)goto done;e=prepare(f);if(e)goto done;
    if(f->count&&!args){e=1;goto done;}
    size_t bytes=48;for(unsigned i=0;i<f->count;i++){
        if(!args[i]||f->sizes[i]>FLYT_MAX_PARAMETER_BYTES||bytes>FLYT_MAX_PARAMETER_BYTES+48-f->sizes[i]-4){e=1;goto done;}
        bytes+=4+f->sizes[i];}
    uint8_t *p=calloc(1,bytes);if(!p){e=2;goto done;}
    flyt_put(p,f->remote,8);flyt_put(p+8,(uintptr_t)stream,8);
    unsigned fields[]={grid.x,grid.y,grid.z,block.x,block.y,block.z,(unsigned)shared,f->count};
    for(unsigned i=0;i<8;i++)flyt_put(p+16+4*i,fields[i],4);
    size_t at=48,got;
    for(unsigned i=0;i<f->count;i++){flyt_put(p+at,f->sizes[i],4);at+=4;memcpy(p+at,args[i],f->sizes[i]);at+=f->sizes[i];}
    e=exchange(FLYT_KERNEL_PACKED,p,bytes,NULL,0,&got);free(p);
done:pthread_mutex_unlock(&registration_lock);
    if(e)fprintf(stderr,"flyt cudaLaunchKernel failed: %d (%s)\n",e,f?f->name:"unregistered function");
    return (cudaError_t)e;
}
cudaError_t cudaLaunchKernelExC(const cudaLaunchConfig_t *config,const void *function,void **args){
    if(!config)return cudaErrorInvalidValue;if(config->numAttrs)return cudaErrorNotSupported;
    return cudaLaunchKernel(function,config->gridDim,config->blockDim,args,config->dynamicSmemBytes,config->stream);
}
