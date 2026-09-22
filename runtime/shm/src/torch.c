#include "flyt_torch.h"
#include <cuda_runtime_api.h>
#include <cuda.h>
#include <string.h>
int flyt_async_stream(uint64_t,void **);

int flyt_torch_dispatch(struct flyt_cuda_session *s,const struct flyt_shm_request *q,struct flyt_shm_response *r) {
    const uint8_t *p=q->input;size_t n=q->input_bytes,at=0;
    r->output_bytes=0;r->api_result=0;r->result_domain=FLYT_RESULT_CUDA_RUNTIME;
    if(q->payload_schema!=1)goto unsupported;
    switch(q->api_id) {
    case FLYT_PRIMARY_CONTEXT_STATE: {
        r->result_domain=FLYT_RESULT_CUDA_DRIVER;
        if(n||r->output_capacity<8||!r->output)goto invalid;unsigned flags=0;int active=0;
        r->api_result=cuDevicePrimaryCtxGetState(0,&flags,&active);
        if(!r->api_result){flyt_put(r->output,flags,4);flyt_put(r->output+4,(uint32_t)active,4);r->output_bytes=8;}break;
    }
    case FLYT_DEVICE_PROPERTIES: {
        if(n!=4||flyt_get(p,4)!=12080||r->output_capacity<FLYT_PROPERTY_CAPACITY||!r->output)goto invalid;
        struct cudaDeviceProp prop={0};
        r->api_result=cudaGetDeviceProperties(&prop,0);if(r->api_result)break;
        /* These native host capabilities cannot cross the VM channel. */
        prop.managedMemory=0;prop.concurrentManagedAccess=0;prop.pageableMemoryAccess=0;
        prop.canMapHostMemory=0;prop.canUseHostPointerForRegisteredMem=0;
        prop.pageableMemoryAccessUsesHostPageTables=0;prop.directManagedMemAccessFromHost=0;
        prop.hostRegisterSupported=0;prop.memoryPoolsSupported=0;prop.ipcEventSupported=0;
#define FLYT_PROP_NUMBER(field,width) flyt_put(r->output+at,prop.field,width);at+=width;
#define FLYT_PROP_BYTES(field,bytes) memcpy(r->output+at,prop.field,bytes);at+=bytes;
#include "flyt_properties.inc"
#undef FLYT_PROP_NUMBER
#undef FLYT_PROP_BYTES
        r->output_bytes=at;break;
    }
    case FLYT_DEVICE_ATTRIBUTE: {
        if(n!=4||r->output_capacity<4||!r->output)goto invalid;int value=0;
        enum cudaDeviceAttr attr=(enum cudaDeviceAttr)flyt_get(p,4);
        r->api_result=cudaDeviceGetAttribute(&value,attr,0);
        if(attr==cudaDevAttrManagedMemory||attr==cudaDevAttrConcurrentManagedAccess||
           attr==cudaDevAttrPageableMemoryAccess||attr==cudaDevAttrCanMapHostMemory||
           attr==cudaDevAttrCanUseHostPointerForRegisteredMem||attr==cudaDevAttrHostRegisterSupported||
           attr==cudaDevAttrMemoryPoolsSupported||attr==cudaDevAttrIpcEventSupport) value=0;
        if(!r->api_result){flyt_put(r->output,(uint32_t)value,4);r->output_bytes=4;}break;
    }
    case FLYT_CUDA_VERSION: {
        if(n!=4||flyt_get(p,4)>1||r->output_capacity<4||!r->output)goto invalid;int value=0;
        r->api_result=flyt_get(p,4)?cudaDriverGetVersion(&value):cudaRuntimeGetVersion(&value);
        if(!r->api_result){flyt_put(r->output,(uint32_t)value,4);r->output_bytes=4;}break;
    }
    case FLYT_ALLOCATION_ADDRESS: {
        if(n!=8||r->output_capacity<8||!r->output)goto invalid;
        void *address=NULL;struct flyt_device_ref ref={flyt_get(p,8),0};
        if(!flyt_cuda_exec_resolve(s->exec,ref,0,&address))goto invalid;
        /* Explicit device-VA token for opt-in mirror mode, never a CPU address
         * dereferenced by the receiver. Legacy handle-only APIs are unchanged. */
        flyt_put(r->output,(uintptr_t)address,8);r->output_bytes=8;break;
    }
    case FLYT_MEMSET: {
        if(n!=40||flyt_get(p+36,4))goto invalid;
        struct flyt_device_ref ref={flyt_get(p,8),flyt_get(p+8,8)};
        size_t bytes=(size_t)flyt_get(p+16,8);void *address=NULL,*stream=NULL;
        if(!flyt_cuda_exec_resolve(s->exec,ref,bytes,&address)||!flyt_async_stream(flyt_get(p+24,8),&stream))goto invalid;
        r->api_result=cudaMemsetAsync(address,(int)flyt_get(p+32,4),bytes,(cudaStream_t)stream);break;
    }
    case FLYT_PRIORITY_RANGE: {
        if(n||r->output_capacity<8||!r->output)goto invalid;int least=0,greatest=0;
        r->api_result=cudaDeviceGetStreamPriorityRange(&least,&greatest);
        if(!r->api_result){flyt_put(r->output,(uint32_t)least,4);flyt_put(r->output+4,(uint32_t)greatest,4);r->output_bytes=8;}break;
    }
    default:goto unsupported;
    }
    return 0;
invalid:r->transport_status=FLYT_SHM_BAD_DESCRIPTOR;goto error;
unsupported:r->transport_status=FLYT_SHM_UNSUPPORTED_API;
error:r->result_domain=0;r->api_result=0;r->output_bytes=0;return 0;
}
