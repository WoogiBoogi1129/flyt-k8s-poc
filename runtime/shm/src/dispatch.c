#include "flyt_wire.h"
#include <string.h>
int flyt_cuda_dispatch(struct flyt_cuda_session *s,const struct flyt_shm_request *q,struct flyt_shm_response *r){
    struct flyt_cuda_call c={0};struct flyt_cuda_result result={0};size_t need=0;int rc;
    if(!s||!q||!r)return FLYT_SHM_BAD_DESCRIPTOR;
    r->output_bytes=0;r->transport_status=0;r->result_domain=0;r->api_result=0;
    if(q->payload_schema!=1){r->transport_status=FLYT_SHM_UNSUPPORTED_API;return 0;}
    c.api_id=q->api_id;
    switch(c.api_id){
    case FLYT_API_RUNTIME_GET_DEVICE_COUNT:case FLYT_API_RUNTIME_GET_DEVICE:
        if(q->input_bytes)goto invalid;need=4;break;
    case FLYT_API_RUNTIME_SET_DEVICE:
        if(q->input_bytes!=4)goto invalid;c.args.device=(int32_t)flyt_get(q->input,4);break;
    case FLYT_API_RUNTIME_MALLOC:
        if(q->input_bytes!=8)goto invalid;c.args.allocation_bytes=flyt_get(q->input,8);need=8;break;
    case FLYT_API_RUNTIME_FREE:
        if(q->input_bytes!=8)goto invalid;c.args.free_handle=flyt_get(q->input,8);break;
    case FLYT_API_RUNTIME_DEVICE_SYNCHRONIZE:if(q->input_bytes)goto invalid;break;
    case FLYT_API_RUNTIME_MEMCPY:{
        if(q->input_bytes<48||flyt_get(q->input+4,4))goto invalid;
        uint64_t n=flyt_get(q->input+40,8);uint32_t k=(uint32_t)flyt_get(q->input,4);
        if(n>FLYT_EXEC_MAX_COPY_BYTES||k<1||k>3)goto invalid;
        if(q->input_bytes!=48+(k==FLYT_COPY_HTOD?(size_t)n:0))goto invalid;
        c.args.copy.direction=k;c.args.copy.dst.handle=flyt_get(q->input+8,8);c.args.copy.dst.offset=flyt_get(q->input+16,8);
        c.args.copy.src.handle=flyt_get(q->input+24,8);c.args.copy.src.offset=flyt_get(q->input+32,8);c.args.copy.bytes=n;
        if(k==FLYT_COPY_HTOD){c.args.copy.host_input=q->input+48;c.args.copy.host_input_bytes=(size_t)n;}
        if(k==FLYT_COPY_DTOH)need=(size_t)n;break;}
    default:r->transport_status=FLYT_SHM_UNSUPPORTED_API;return 0;
    }
    /* Reserve caller's response space BEFORE any CUDA side effect. */
    if(need>r->output_capacity||(need&&!r->output))goto invalid;
    rc=flyt_cuda_exec_call(s->exec,&c,&result);
    r->transport_status=(uint32_t)rc;r->result_domain=result.result_domain;r->api_result=result.api_result;
    if(!rc&&!result.api_result){
        if(c.api_id==FLYT_API_RUNTIME_MALLOC)flyt_put(r->output,result.handle,8);
        else if(c.api_id==FLYT_API_RUNTIME_GET_DEVICE_COUNT||c.api_id==FLYT_API_RUNTIME_GET_DEVICE)flyt_put(r->output,(uint32_t)result.device,4);
        else if(result.data_bytes){memcpy(r->output,result.data,result.data_bytes);}
        r->output_bytes=need;
    }
    flyt_cuda_result_release(&result);return 0;
invalid:r->transport_status=FLYT_SHM_BAD_DESCRIPTOR;return 0;
}
