#include "flyt_cuda_exec.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t count(void *c,int32_t *v){(void)c;*v=1;return 0;}
static uint32_t device(void *c,int32_t *v){(void)c;*v=0;return 0;}
static uint32_t set(void *c,int32_t v){(void)c;return v?1:0;}
static uint32_t alloc(void *c,void **p,size_t n){(void)c;*p=NULL;if(n>1024)return 2;*p=malloc(n);return *p?0:2;}
static uint32_t release(void *c,void *p){(void)c;free(p);return 0;}
static uint32_t copy(void *c,void *d,const void *s,size_t n,uint32_t k){(void)c;(void)k;memcpy(d,s,n);return 0;}
static uint32_t sync_gpu(void *c){(void)c;return 0;}
int main(void){
    struct flyt_cuda_backend backend={count,device,set,alloc,release,copy,sync_gpu};
    struct flyt_cuda_exec *s=NULL;struct flyt_cuda_result result;
    assert(!flyt_cuda_exec_create(&backend,NULL,&s));
    struct flyt_cuda_call call={.api_id=FLYT_API_RUNTIME_MALLOC,.args.allocation_bytes=16};
    assert(!flyt_cuda_exec_call(s,&call,&result)&&!result.api_result);
    uint64_t retained=result.handle;flyt_cuda_result_release(&result);
    call.args.allocation_bytes=2048;
    assert(!flyt_cuda_exec_call(s,&call,&result)&&result.api_result==2&&!result.handle);
    flyt_cuda_result_release(&result);
    void *pointer=NULL;
    assert(flyt_cuda_exec_resolve(s,(struct flyt_device_ref){retained,0},16,&pointer));
    assert(pointer);
    call.api_id=FLYT_API_RUNTIME_FREE;call.args.free_handle=retained;
    assert(!flyt_cuda_exec_call(s,&call,&result)&&!result.api_result);
    flyt_cuda_result_release(&result);
    call.api_id=FLYT_API_RUNTIME_MALLOC;call.args.allocation_bytes=1024;
    assert(!flyt_cuda_exec_call(s,&call,&result)&&!result.api_result&&result.handle!=retained);
    flyt_cuda_result_release(&result);
    uint32_t error=0;assert(!flyt_cuda_exec_destroy(&s,&error)&&!error&&!s);
    puts("PASS: OOM preserves existing allocation, free, retry and clean shutdown");
}
