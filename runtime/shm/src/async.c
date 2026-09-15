#include "flyt_async.h"
#include <cuda_runtime_api.h>
#include <cuda.h>
#include <stdlib.h>
#include <string.h>

enum {STREAM=1,EVENT,MODULE,FUNCTION};
struct object {uint64_t id,parent;int kind;void *pointer;};
static struct object objects[4096];static uint64_t next=1;
struct stage {void *host;cudaEvent_t done;};static struct stage stages[128];
static struct object *find(uint64_t id,int kind){for(int i=0;i<4096;i++)if(objects[i].id==id&&id&&objects[i].kind==kind)return &objects[i];return NULL;}
static struct object *reserve(void){if(next==UINT64_MAX)return NULL;for(int i=0;i<4096;i++)if(!objects[i].id)return &objects[i];return NULL;}
static int stream(uint64_t id,cudaStream_t *out){struct object *o;if(!id){*out=0;return 1;}o=find(id,STREAM);if(!o)return 0;*out=(cudaStream_t)o->pointer;return 1;}
int flyt_async_reap(void){
    for(int i=0;i<128;i++)if(stages[i].host){cudaError_t e=cudaEventQuery(stages[i].done);
        if(e==cudaErrorNotReady)continue;if(e!=cudaSuccess)return -1;
        if(cudaEventDestroy(stages[i].done)!=cudaSuccess||cudaFreeHost(stages[i].host)!=cudaSuccess)return -1;
        memset(&stages[i],0,sizeof(stages[i]));}return 0;
}
int flyt_async_dispatch(struct flyt_cuda_session *s,const struct flyt_shm_request *q,struct flyt_shm_response *r){
    const uint8_t *p=q->input;size_t n=q->input_bytes;struct object *o=NULL;cudaStream_t st=0;uint64_t id=0;
    r->output_bytes=0;r->api_result=0;r->result_domain=FLYT_RESULT_CUDA_RUNTIME;r->transport_status=0;
    if(q->payload_schema!=1)goto unsupported;
    switch(q->api_id){
    case FLYT_STREAM_CREATE:case FLYT_EVENT_CREATE:
        if(n!=4||r->output_capacity<8||!r->output)goto invalid;o=reserve();if(!o)goto internal;
        if(q->api_id==FLYT_STREAM_CREATE){cudaStream_t v=NULL;r->api_result=cudaStreamCreateWithFlags(&v,(unsigned)flyt_get(p,4));o->pointer=v;o->kind=STREAM;}
        else{cudaEvent_t v=NULL;r->api_result=cudaEventCreateWithFlags(&v,(unsigned)flyt_get(p,4));o->pointer=v;o->kind=EVENT;}
        if(!r->api_result){o->id=next++;flyt_put(r->output,o->id,8);r->output_bytes=8;}break;
    case FLYT_STREAM_DESTROY:case FLYT_STREAM_SYNC:case FLYT_STREAM_QUERY:
        if(n!=8||!stream(flyt_get(p,8),&st))goto invalid;
        if(q->api_id==FLYT_STREAM_SYNC)r->api_result=cudaStreamSynchronize(st);
        else if(q->api_id==FLYT_STREAM_QUERY)r->api_result=cudaStreamQuery(st);
        else{if(!st)goto invalid;o=find(flyt_get(p,8),STREAM);r->api_result=cudaStreamDestroy(st);if(!r->api_result)memset(o,0,sizeof(*o));}break;
    case FLYT_EVENT_DESTROY:case FLYT_EVENT_SYNC:case FLYT_EVENT_QUERY:case FLYT_EVENT_RECORD:
        if(n!=(q->api_id==FLYT_EVENT_RECORD?16:8)||(o=find(flyt_get(p,8),EVENT))==NULL)goto invalid;
        if(q->api_id==FLYT_EVENT_RECORD){if(!stream(flyt_get(p+8,8),&st))goto invalid;r->api_result=cudaEventRecord((cudaEvent_t)o->pointer,st);}
        else if(q->api_id==FLYT_EVENT_SYNC)r->api_result=cudaEventSynchronize((cudaEvent_t)o->pointer);
        else if(q->api_id==FLYT_EVENT_QUERY)r->api_result=cudaEventQuery((cudaEvent_t)o->pointer);
        else{r->api_result=cudaEventDestroy((cudaEvent_t)o->pointer);if(!r->api_result)memset(o,0,sizeof(*o));}break;
    case FLYT_ASYNC_COPY:{
        if(n<56||flyt_get(p+12,4)||!stream(flyt_get(p,8),&st))goto invalid;
        unsigned k=(unsigned)flyt_get(p+8,4);uint64_t bytes=flyt_get(p+48,8);void *dst=NULL,*src=NULL;
        struct flyt_device_ref d={flyt_get(p+16,8),flyt_get(p+24,8)},a={flyt_get(p+32,8),flyt_get(p+40,8)};
        if(k<1||k>3||bytes>FLYT_EXEC_MAX_COPY_BYTES||n!=56+(k==1?(size_t)bytes:0))goto invalid;
        if(k!=2&&!flyt_cuda_exec_resolve(s->exec,d,(size_t)bytes,&dst))goto invalid;
        if(k!=1&&!flyt_cuda_exec_resolve(s->exec,a,(size_t)bytes,&src))goto invalid;
        if(k==3&&bytes&&d.handle==a.handle&&(d.offset>=a.offset?d.offset-a.offset:a.offset-d.offset)<bytes)goto invalid;
        if(k==2&&(bytes>r->output_capacity||(bytes&&!r->output)))goto invalid;
        if(!bytes)break;
        if(k==1){int i;for(i=0;i<128&&stages[i].host;i++);if(i==128)goto internal;
            void *host=NULL;cudaEvent_t event=NULL;
            r->api_result=cudaHostAlloc(&host,(size_t)bytes,cudaHostAllocDefault);if(r->api_result)break;
            r->api_result=cudaEventCreateWithFlags(&event,cudaEventDisableTiming);
            if(r->api_result){cudaFreeHost(host);break;}
            memcpy(host,p+56,(size_t)bytes);
            r->api_result=cudaMemcpyAsync(dst,host,(size_t)bytes,cudaMemcpyHostToDevice,st);
            if(!r->api_result)r->api_result=cudaEventRecord(event,st);
            if(r->api_result){/* Cannot free potentially in-flight staging. Exit this process. */return -1;}
            stages[i].host=host;stages[i].done=event;
        }else if(k==2){
            /* Conservative completion: this initial DtoH path blocks until stream
             * work completes and bytes can be returned, never returns stale data. */
            r->api_result=cudaStreamSynchronize(st);
            if(!r->api_result)r->api_result=cudaMemcpy(r->output,src,(size_t)bytes,cudaMemcpyDeviceToHost);
            if(!r->api_result)r->output_bytes=(size_t)bytes;
        }else r->api_result=cudaMemcpyAsync(dst,src,(size_t)bytes,cudaMemcpyDeviceToDevice,st);
        break;}
    case FLYT_MODULE_LOAD:{
        r->result_domain=FLYT_RESULT_CUDA_DRIVER;
        if(!n||n>FLYT_EXEC_MAX_COPY_BYTES||p[n-1]||memchr(p,0,n-1)||r->output_capacity<8||!r->output)goto invalid;
        o=reserve();if(!o)goto internal;CUmodule m=NULL;r->api_result=cuModuleLoadData(&m,p);
        if(!r->api_result){o->kind=MODULE;o->pointer=m;o->id=next++;flyt_put(r->output,o->id,8);r->output_bytes=8;}break;}
    case FLYT_MODULE_UNLOAD:
        r->result_domain=FLYT_RESULT_CUDA_DRIVER;if(n!=8||(o=find(flyt_get(p,8),MODULE))==NULL)goto invalid;
        r->api_result=cuCtxSynchronize();if(!r->api_result)r->api_result=cuModuleUnload((CUmodule)o->pointer);
        if(!r->api_result){id=o->id;memset(o,0,sizeof(*o));for(int i=0;i<4096;i++)if(objects[i].parent==id)memset(&objects[i],0,sizeof(objects[i]));}break;
    case FLYT_FUNCTION_GET:{
        r->result_domain=FLYT_RESULT_CUDA_DRIVER;if(n<10||n>264||p[n-1]||memchr(p+8,0,n-9)||r->output_capacity<8||!r->output)goto invalid;
        struct object *m=find(flyt_get(p,8),MODULE);if(!m)goto invalid;o=reserve();if(!o)goto internal;
        CUfunction f=NULL;r->api_result=cuModuleGetFunction(&f,(CUmodule)m->pointer,(const char *)p+8);
        if(!r->api_result){o->id=next++;o->kind=FUNCTION;o->parent=m->id;o->pointer=f;flyt_put(r->output,o->id,8);r->output_bytes=8;}break;}
    case FLYT_KERNEL_LAUNCH:{
        r->result_domain=FLYT_RESULT_CUDA_DRIVER;if(n<48||(o=find(flyt_get(p,8),FUNCTION))==NULL||!stream(flyt_get(p+8,8),&st))goto invalid;
        uint32_t count=(uint32_t)flyt_get(p+44,4);if(count>64)goto invalid;
        void *params[64];unsigned char values[64][16];size_t pos=48;
        for(unsigned i=0;i<count;i++){
            if(n-pos<24)goto invalid;unsigned type=(unsigned)flyt_get(p+pos,4),size=(unsigned)flyt_get(p+pos+4,4);
            if(!size||size>16)goto invalid;
            if(type==1){void *address;struct flyt_device_ref a={flyt_get(p+pos+8,8),flyt_get(p+pos+16,8)};
                if(size!=8||!flyt_cuda_exec_resolve(s->exec,a,0,&address))goto invalid;memcpy(values[i],&address,8);}
            else if(type==0)memcpy(values[i],p+pos+8,size);else goto invalid;
            params[i]=values[i];pos+=24;
        }
        if(pos!=n)goto invalid;
        r->api_result=cuLaunchKernel((CUfunction)o->pointer,(unsigned)flyt_get(p+16,4),(unsigned)flyt_get(p+20,4),(unsigned)flyt_get(p+24,4),
            (unsigned)flyt_get(p+28,4),(unsigned)flyt_get(p+32,4),(unsigned)flyt_get(p+36,4),(unsigned)flyt_get(p+40,4),(CUstream)st,params,NULL);break;}
    default:goto unsupported;
    }
    return 0;
invalid:r->transport_status=FLYT_SHM_BAD_DESCRIPTOR;goto error;
unsupported:r->transport_status=FLYT_SHM_UNSUPPORTED_API;goto error;
internal:r->transport_status=FLYT_SHM_INTERNAL_ERROR;
error:r->result_domain=0;r->api_result=0;r->output_bytes=0;return 0;
}
int flyt_async_close(void){
    if(cudaDeviceSynchronize()!=cudaSuccess)return -1;
    if(flyt_async_reap())return -1;
    for(int i=0;i<4096;i++)if(objects[i].id){int e=0;
        if(objects[i].kind==STREAM)e=cudaStreamDestroy((cudaStream_t)objects[i].pointer);
        if(objects[i].kind==EVENT)e=cudaEventDestroy((cudaEvent_t)objects[i].pointer);
        if(objects[i].kind==MODULE)e=cuModuleUnload((CUmodule)objects[i].pointer);
        if(e)return -1;memset(&objects[i],0,sizeof(objects[i]));}
    return 0;
}
