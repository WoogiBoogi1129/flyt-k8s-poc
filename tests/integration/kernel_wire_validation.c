/* Pure host validation of new wire bounds. CUDA launch is mocked; the actual
 * compiled-kernel and MLP evidence is collected separately inside a VM. */
#include "../../runtime/shm/src/async.c"
#include <assert.h>
#include <stdio.h>
static unsigned launches;
static unsigned char expected[64];
CUresult cuFuncGetParamInfo(CUfunction f,size_t index,size_t *offset,size_t *size){
    assert(f==(CUfunction)(uintptr_t)123);
    if(index>=2)return CUDA_ERROR_INVALID_VALUE;
    *offset=index?8:0;*size=index?64:4;return CUDA_SUCCESS;
}
CUresult cuLaunchKernel(CUfunction f,unsigned gx,unsigned gy,unsigned gz,unsigned bx,unsigned by,unsigned bz,
                       unsigned shared,CUstream stream,void **params,void **extra){
    (void)gy;(void)gz;(void)by;(void)bz;(void)shared;(void)stream;
    assert(f==(CUfunction)(uintptr_t)123&&gx==1&&bx==1&&!extra);
    assert(*(unsigned*)params[0]==42&&!memcmp(params[1],expected,64));++launches;return CUDA_SUCCESS;
}
cudaError_t cudaDeviceSynchronize(void){return cudaSuccess;}
int flyt_cuda_exec_resolve(struct flyt_cuda_exec *s,struct flyt_device_ref r,size_t n,void **p){(void)s;(void)r;(void)n;(void)p;return 0;}
int main(void){
    struct flyt_cuda_session s={0};unsigned char input[256]={0},output[512]={0};
    struct flyt_shm_request q={.api_id=FLYT_KERNEL_PACKED,.payload_schema=1,.input=input,.input_bytes=124};
    struct flyt_shm_response r={.output=output,.output_capacity=sizeof(output)};
    objects[0]=(struct object){.id=7,.kind=FUNCTION,.pointer=(void*)(uintptr_t)123};
    flyt_put(input,7,8);flyt_put(input+16,1,4);flyt_put(input+20,1,4);flyt_put(input+24,1,4);
    flyt_put(input+28,1,4);flyt_put(input+32,1,4);flyt_put(input+36,1,4);flyt_put(input+44,2,4);
    flyt_put(input+48,4,4);flyt_put(input+52,42,4);flyt_put(input+56,64,4);
    for(unsigned i=0;i<64;i++)expected[i]=(unsigned char)(i+1);
    memcpy(input+60,expected,64);
    assert(!flyt_async_dispatch(&s,&q,&r)&&!r.transport_status&&!r.api_result&&launches==1);
    q.input_bytes--;assert(!flyt_async_dispatch(&s,&q,&r)&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR&&launches==1);q.input_bytes++;
    flyt_put(input+56,63,4);assert(!flyt_async_dispatch(&s,&q,&r)&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR&&launches==1);flyt_put(input+56,64,4);
    flyt_put(input+44,1,4);assert(!flyt_async_dispatch(&s,&q,&r)&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR&&launches==1);
    flyt_put(input,999,8);assert(!flyt_async_dispatch(&s,&q,&r)&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR&&launches==1);
    q.api_id=FLYT_MODULE_BEGIN;q.input_bytes=8;flyt_put(input,32,8);
    r.output_capacity=7;assert(!flyt_async_dispatch(&s,&q,&r)&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR&&upload_bytes==0);
    r.output_capacity=sizeof(output);assert(!flyt_async_dispatch(&s,&q,&r)&&!r.transport_status&&upload_bytes==32);
    uint64_t id=flyt_get(output,8);q.api_id=FLYT_MODULE_CHUNK;q.input_bytes=32;
    flyt_put(input,id,8);flyt_put(input+8,1,8);
    assert(!flyt_async_dispatch(&s,&q,&r)&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR&&find(id,UPLOAD)->received==0);
    flyt_put(input+8,0,8);assert(!flyt_async_dispatch(&s,&q,&r)&&!r.transport_status&&find(id,UPLOAD)->received==16);
    q.api_id=FLYT_MODULE_COMMIT;q.input_bytes=8;
    assert(!flyt_async_dispatch(&s,&q,&r)&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR);
    assert(!flyt_async_close()&&upload_bytes==0);
    puts("PASS: packed layout/count/length validation, unknown handles, upload capacity/order/completeness and cleanup");
    return 0;
}
