#include "flyt_wire.h"
#include <cuda_runtime_api.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>

int flyt_cuda_dispatch(struct flyt_cuda_session *,const struct flyt_shm_request *,struct flyt_shm_response *);
static int calls,state;static size_t free_value=123,total_value=456;static cudaError_t error_value=cudaSuccess;
int flyt_cuda_exec_check(struct flyt_cuda_exec *s){(void)s;return state;}
cudaError_t cudaMemGetInfo(size_t *free_bytes,size_t *total_bytes){calls++;*free_bytes=free_value;*total_bytes=total_value;return error_value;}
int flyt_cuda_exec_call(struct flyt_cuda_exec *e,const struct flyt_cuda_call *c,struct flyt_cuda_result *r){(void)e;(void)c;(void)r;abort();}
void flyt_cuda_result_release(struct flyt_cuda_result *r){(void)r;abort();}
int main(void){
    struct flyt_cuda_session session={.exec=(struct flyt_cuda_exec *)1};
    struct flyt_shm_request q={.api_id=FLYT_API_RUNTIME_MEM_GET_INFO,.payload_schema=1};
    uint8_t output[16];struct flyt_shm_response r={.output=output,.output_capacity=16};
    assert(!flyt_cuda_dispatch(&session,&q,&r));assert(calls==1&&r.output_bytes==16&&!r.api_result);
    assert(flyt_get(output,8)==123&&flyt_get(output+8,8)==456);
    r.output_capacity=15;assert(!flyt_cuda_dispatch(&session,&q,&r));assert(calls==1&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR);
    r.output_capacity=16;q.input_bytes=1;assert(!flyt_cuda_dispatch(&session,&q,&r));assert(calls==1&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR);
    q.input_bytes=0;error_value=cudaErrorMemoryAllocation;assert(!flyt_cuda_dispatch(&session,&q,&r));assert(r.api_result==cudaErrorMemoryAllocation&&r.output_bytes==0);
    error_value=cudaSuccess;free_value=457;assert(!flyt_cuda_dispatch(&session,&q,&r));assert(r.api_result==cudaErrorUnknown&&r.output_bytes==0);
    int previous=calls;state=FLYT_SHM_CHANNEL_CLOSED;
    assert(!flyt_cuda_dispatch(&session,&q,&r));assert(calls==previous&&r.transport_status==FLYT_SHM_CHANNEL_CLOSED);
    state=FLYT_SHM_BAD_DESCRIPTOR;
    assert(!flyt_cuda_dispatch(&session,&q,&r));assert(calls==previous&&r.transport_status==FLYT_SHM_BAD_DESCRIPTOR);
    puts("PASS: memory-info encoding, bounds, CUDA errors, inconsistent observations and closed/wrong-thread guards");
    return 0;
}
