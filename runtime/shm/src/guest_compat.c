#include "flyt_guest.h"
#include "flyt_compat.h"
#include <cuda_runtime_api.h>
#include <cuda.h>
#include <cublas_v2.h>
#include <cudnn.h>
#include <string.h>

static int call(uint32_t api,const void *p,size_t n,void **out){
    uint8_t result[8];size_t got;pthread_mutex_lock(&flyt_guest_lock);
    int e=flyt_guest_exchange(api,p,n,result,sizeof(result),&got);
    if(!e&&out){if(got!=8)e=999;else *out=(void *)(uintptr_t)flyt_get(result,8);}
    pthread_mutex_unlock(&flyt_guest_lock);return e;
}
static int one(uint32_t api,void *h,void **out){uint8_t p[8];flyt_put(p,(uintptr_t)h,8);return call(api,p,8,out);}
cudaError_t cudaGraphCreate(cudaGraph_t *g,unsigned flags){if(!g)return cudaErrorInvalidValue;if(flags)return cudaErrorNotSupported;return (cudaError_t)call(FLYT_GRAPH_CREATE,NULL,0,(void **)g);}
cudaError_t cudaGraphDestroy(cudaGraph_t g){return (cudaError_t)one(FLYT_GRAPH_DESTROY,g,NULL);}
cudaError_t cudaGraphExecDestroy(cudaGraphExec_t g){return (cudaError_t)one(FLYT_GRAPH_EXEC_DESTROY,g,NULL);}
cudaError_t cudaGraphAddEmptyNode(cudaGraphNode_t *node,cudaGraph_t graph,const cudaGraphNode_t *deps,size_t count){
    if(!node||count>64||(count&&!deps))return cudaErrorInvalidValue;uint8_t p[12+8*64];flyt_put(p,(uintptr_t)graph,8);flyt_put(p+8,count,4);
    for(size_t i=0;i<count;i++)flyt_put(p+12+8*i,(uintptr_t)deps[i],8);
    return (cudaError_t)call(FLYT_GRAPH_EMPTY_NODE,p,12+8*count,(void **)node);
}
cudaError_t cudaGraphInstantiateWithFlags(cudaGraphExec_t *exec,cudaGraph_t graph,unsigned long long flags){
    if(!exec)return cudaErrorInvalidValue;if(flags)return cudaErrorNotSupported;return (cudaError_t)one(FLYT_GRAPH_INSTANTIATE,graph,(void **)exec);
}
cudaError_t cudaGraphLaunch(cudaGraphExec_t exec,cudaStream_t stream){uint8_t p[16];flyt_put(p,(uintptr_t)exec,8);flyt_put(p+8,(uintptr_t)stream,8);return (cudaError_t)call(FLYT_GRAPH_LAUNCH,p,16,NULL);}
cublasStatus_t cublasCreate_v2(cublasHandle_t *h){return h?(cublasStatus_t)call(FLYT_BLAS_CREATE,NULL,0,(void **)h):CUBLAS_STATUS_INVALID_VALUE;}
cublasStatus_t cublasDestroy_v2(cublasHandle_t h){return (cublasStatus_t)one(FLYT_BLAS_DESTROY,h,NULL);}
cublasStatus_t cublasSetStream_v2(cublasHandle_t h,cudaStream_t stream){uint8_t p[16];flyt_put(p,(uintptr_t)h,8);flyt_put(p+8,(uintptr_t)stream,8);return (cublasStatus_t)call(FLYT_BLAS_STREAM,p,16,NULL);}
cublasStatus_t cublasSgemm_v2(cublasHandle_t h,cublasOperation_t ta,cublasOperation_t tb,int m,int n,int k,const float *alpha,const float *a,int lda,const float *b,int ldb,const float *beta,float *c,int ldc){
    if(!alpha||!beta)return CUBLAS_STATUS_INVALID_VALUE;
    uint8_t p[96]={0};struct flyt_device_ref ar,br,cr;size_t got;
    pthread_mutex_lock(&flyt_guest_lock);int e=CUBLAS_STATUS_INVALID_VALUE;
    if(flyt_guest_ref(a,0,&ar)||flyt_guest_ref(b,0,&br)||flyt_guest_ref(c,0,&cr))goto done;
    flyt_put(p,(uintptr_t)h,8);unsigned fields[]={ta,tb,(unsigned)m,(unsigned)n,(unsigned)k,(unsigned)lda,(unsigned)ldb,(unsigned)ldc};
    for(int i=0;i<8;i++)flyt_put(p+8+i*4,fields[i],4);memcpy(p+40,alpha,4);memcpy(p+44,beta,4);
    flyt_put(p+48,ar.handle,8);flyt_put(p+56,ar.offset,8);flyt_put(p+64,br.handle,8);flyt_put(p+72,br.offset,8);flyt_put(p+80,cr.handle,8);flyt_put(p+88,cr.offset,8);
    e=flyt_guest_exchange(FLYT_BLAS_SGEMM,p,sizeof(p),NULL,0,&got);
done:pthread_mutex_unlock(&flyt_guest_lock);return (cublasStatus_t)e;
}
cudnnStatus_t cudnnCreate(cudnnHandle_t *h){return h?(cudnnStatus_t)call(FLYT_DNN_CREATE,NULL,0,(void **)h):CUDNN_STATUS_BAD_PARAM;}
cudnnStatus_t cudnnDestroy(cudnnHandle_t h){return (cudnnStatus_t)one(FLYT_DNN_DESTROY,h,NULL);}
size_t cudnnGetVersion(void){void *v=NULL;return call(FLYT_DNN_VERSION,NULL,0,&v)?0:(size_t)(uintptr_t)v;}
/* Explicit rejection of commonly reached unsupported paths; no real GPU fallback. */
cudaError_t cudaStreamBeginCapture(cudaStream_t s,enum cudaStreamCaptureMode mode){(void)s;(void)mode;return cudaErrorNotSupported;}
cudaError_t cudaStreamEndCapture(cudaStream_t s,cudaGraph_t *g){(void)s;if(g)*g=NULL;return cudaErrorNotSupported;}
cudaError_t cudaMallocManaged(void **p,size_t n,unsigned f){(void)n;(void)f;if(p)*p=NULL;return cudaErrorNotSupported;}
cudaError_t cudaDeviceReset(void){return cudaErrorNotSupported;}
cudaError_t cudaLaunchKernel(const void *f,struct dim3 grid,struct dim3 block,void **args,size_t shared,cudaStream_t stream){(void)f;(void)grid;(void)block;(void)args;(void)shared;(void)stream;return cudaErrorNotSupported;}
/* Never return an address from a native CUDA library. Version/flags outside the
 * authored ABI are rejected. Missing symbols stay NULL. */
#undef cuGetProcAddress
CUresult cuGetProcAddress(const char *symbol,void **pfn,int version,cuuint64_t flags){
    if(!symbol||!pfn)return CUDA_ERROR_INVALID_VALUE;*pfn=NULL;
    if(flags||version<2000||version>12080)return CUDA_ERROR_NOT_SUPPORTED;
#define SYMBOL(name) if(!strcmp(symbol,#name)){*pfn=(void *)&name;return CUDA_SUCCESS;}
    SYMBOL(cuInit) SYMBOL(cuDeviceGet) SYMBOL(cuDeviceGetCount)
    SYMBOL(cuMemAlloc_v2) SYMBOL(cuMemFree_v2) SYMBOL(cuMemcpyHtoD_v2) SYMBOL(cuMemcpyDtoH_v2) SYMBOL(cuMemcpyDtoD_v2)
    SYMBOL(cuCtxSynchronize) SYMBOL(cuModuleLoadData) SYMBOL(cuModuleUnload) SYMBOL(cuModuleGetFunction) SYMBOL(cuLaunchKernel)
#undef SYMBOL
    return CUDA_ERROR_NOT_SUPPORTED;
}
CUresult cuGetProcAddress_v2(const char *symbol,void **pfn,int version,cuuint64_t flags,CUdriverProcAddressQueryResult *status){
    CUresult e=cuGetProcAddress(symbol,pfn,version,flags);
    if(status)*status=e==CUDA_SUCCESS?CU_GET_PROC_ADDRESS_SUCCESS:CU_GET_PROC_ADDRESS_SYMBOL_NOT_FOUND;
    return e;
}
