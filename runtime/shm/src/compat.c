#include "flyt_compat.h"
#include <cuda_runtime_api.h>
#include <cublas_v2.h>
#include <cudnn.h>
#include <string.h>
enum {GRAPH=1,NODE,GRAPH_EXEC,BLAS,DNN};
static struct {uint64_t id,parent;int kind;void *value;} handles[4096];
static uint64_t next=UINT64_C(0x4000000000000001);
static int lookup(uint64_t id,int kind){for(int i=0;i<4096;i++)if(id&&handles[i].id==id&&handles[i].kind==kind)return i;return -1;}
static int slot(void){if(next==UINT64_MAX)return -1;for(int i=0;i<4096;i++)if(!handles[i].id)return i;return -1;}
static void created(int i,int kind,void *p,uint64_t parent,struct flyt_shm_response *r){handles[i].id=next++;handles[i].kind=kind;handles[i].value=p;handles[i].parent=parent;flyt_put(r->output,handles[i].id,8);r->output_bytes=8;}
static int matrix(struct flyt_cuda_session *s,const uint8_t *p,int rows,int cols,int ld,void **out){
    if(rows<=0||cols<=0||ld<rows)return 0;
    uint64_t elements=(uint64_t)ld*(unsigned)(cols-1)+(unsigned)rows;
    if(elements>SIZE_MAX/4)return 0;struct flyt_device_ref ref={flyt_get(p,8),flyt_get(p+8,8)};
    return flyt_cuda_exec_resolve(s->exec,ref,(size_t)elements*4,out);
}
int flyt_compat_dispatch(struct flyt_cuda_session *s,const struct flyt_shm_request *q,struct flyt_shm_response *r){
    const uint8_t *p=q->input;size_t n=q->input_bytes;int i=-1;void *st=NULL;
    r->output_bytes=0;r->transport_status=0;r->api_result=0;r->result_domain=q->api_id>=0x3100?FLYT_RESULT_LIBRARY:FLYT_RESULT_CUDA_RUNTIME;
    if(q->payload_schema!=1)goto unsupported;
    switch(q->api_id){
    case FLYT_GRAPH_CREATE:case FLYT_BLAS_CREATE:case FLYT_DNN_CREATE:
        if(n||r->output_capacity<8||!r->output||(i=slot())<0)goto invalid;
        if(q->api_id==FLYT_GRAPH_CREATE){cudaGraph_t g=NULL;r->api_result=cudaGraphCreate(&g,0);if(!r->api_result)created(i,GRAPH,g,0,r);}
        else if(q->api_id==FLYT_BLAS_CREATE){cublasHandle_t h=NULL;r->api_result=cublasCreate(&h);if(!r->api_result)created(i,BLAS,h,0,r);}
        else{cudnnHandle_t h=NULL;r->api_result=cudnnCreate(&h);if(!r->api_result)created(i,DNN,h,0,r);}break;
    case FLYT_GRAPH_DESTROY:case FLYT_GRAPH_EXEC_DESTROY:case FLYT_BLAS_DESTROY:case FLYT_DNN_DESTROY:{
        int kind=q->api_id==FLYT_GRAPH_DESTROY?GRAPH:q->api_id==FLYT_GRAPH_EXEC_DESTROY?GRAPH_EXEC:q->api_id==FLYT_BLAS_DESTROY?BLAS:DNN;
        if(n!=8||(i=lookup(flyt_get(p,8),kind))<0)goto invalid;
        if(kind==GRAPH)r->api_result=cudaGraphDestroy((cudaGraph_t)handles[i].value);
        else if(kind==GRAPH_EXEC){r->api_result=cudaDeviceSynchronize();if(!r->api_result)r->api_result=cudaGraphExecDestroy((cudaGraphExec_t)handles[i].value);}
        else if(kind==BLAS)r->api_result=cublasDestroy((cublasHandle_t)handles[i].value);
        else r->api_result=cudnnDestroy((cudnnHandle_t)handles[i].value);
        if(!r->api_result){uint64_t id=handles[i].id;memset(&handles[i],0,sizeof(handles[i]));if(kind==GRAPH)for(int j=0;j<4096;j++)if(handles[j].kind==NODE&&handles[j].parent==id)memset(&handles[j],0,sizeof(handles[j]));}break;}
    case FLYT_GRAPH_EMPTY_NODE:{
        if(n<12||r->output_capacity<8||!r->output||(i=lookup(flyt_get(p,8),GRAPH))<0)goto invalid;
        unsigned count=(unsigned)flyt_get(p+8,4);if(count>64||n!=12+8*count)goto invalid;
        cudaGraphNode_t deps[64],node=NULL;for(unsigned j=0;j<count;j++){int d=lookup(flyt_get(p+12+8*j,8),NODE);if(d<0||handles[d].parent!=handles[i].id)goto invalid;deps[j]=(cudaGraphNode_t)handles[d].value;}
        int fresh=slot();if(fresh<0)goto invalid;r->api_result=cudaGraphAddEmptyNode(&node,(cudaGraph_t)handles[i].value,deps,count);
        if(!r->api_result)created(fresh,NODE,node,handles[i].id,r);break;}
    case FLYT_GRAPH_INSTANTIATE:{
        if(n!=8||r->output_capacity<8||!r->output||(i=lookup(flyt_get(p,8),GRAPH))<0)goto invalid;
        int fresh=slot();if(fresh<0)goto invalid;cudaGraphExec_t e=NULL;r->api_result=cudaGraphInstantiateWithFlags(&e,(cudaGraph_t)handles[i].value,0);
        if(!r->api_result)created(fresh,GRAPH_EXEC,e,0,r);break;}
    case FLYT_GRAPH_LAUNCH:
        if(n!=16||(i=lookup(flyt_get(p,8),GRAPH_EXEC))<0||!flyt_async_stream(flyt_get(p+8,8),&st))goto invalid;
        r->api_result=cudaGraphLaunch((cudaGraphExec_t)handles[i].value,(cudaStream_t)st);break;
    case FLYT_BLAS_STREAM:
        if(n!=16||(i=lookup(flyt_get(p,8),BLAS))<0||!flyt_async_stream(flyt_get(p+8,8),&st))goto invalid;
        r->api_result=cublasSetStream((cublasHandle_t)handles[i].value,(cudaStream_t)st);break;
    case FLYT_BLAS_SGEMM:{
        if(n!=96||(i=lookup(flyt_get(p,8),BLAS))<0)goto invalid;
        unsigned ta=(unsigned)flyt_get(p+8,4),tb=(unsigned)flyt_get(p+12,4);if(ta>2||tb>2)goto invalid;
        int m=(int)flyt_get(p+16,4),nn=(int)flyt_get(p+20,4),k=(int)flyt_get(p+24,4);
        int lda=(int)flyt_get(p+28,4),ldb=(int)flyt_get(p+32,4),ldc=(int)flyt_get(p+36,4);
        float alpha,beta;memcpy(&alpha,p+40,4);memcpy(&beta,p+44,4);void *a,*b,*c;
        if(!matrix(s,p+48,ta?k:m,ta?m:k,lda,&a)||!matrix(s,p+64,tb?nn:k,tb?k:nn,ldb,&b)||!matrix(s,p+80,m,nn,ldc,&c))goto invalid;
        r->api_result=cublasSgemm((cublasHandle_t)handles[i].value,(cublasOperation_t)ta,(cublasOperation_t)tb,m,nn,k,&alpha,a,lda,b,ldb,&beta,c,ldc);break;}
    case FLYT_DNN_VERSION:
        if(n||r->output_capacity<8||!r->output)goto invalid;flyt_put(r->output,cudnnGetVersion(),8);r->output_bytes=8;break;
    default:goto unsupported;
    }return 0;
invalid:r->transport_status=FLYT_SHM_BAD_DESCRIPTOR;goto fail;
unsupported:r->transport_status=FLYT_SHM_UNSUPPORTED_API;
fail:r->result_domain=0;r->api_result=0;r->output_bytes=0;return 0;
}
int flyt_compat_close(void){
    if(cudaDeviceSynchronize()!=cudaSuccess)return -1;
    for(int i=0;i<4096;i++)if(handles[i].id){int e=0;
        if(handles[i].kind==GRAPH)e=cudaGraphDestroy((cudaGraph_t)handles[i].value);
        if(handles[i].kind==GRAPH_EXEC)e=cudaGraphExecDestroy((cudaGraphExec_t)handles[i].value);
        if(handles[i].kind==BLAS)e=cublasDestroy((cublasHandle_t)handles[i].value);
        if(handles[i].kind==DNN)e=cudnnDestroy((cudnnHandle_t)handles[i].value);
        if(e)return -1;memset(&handles[i],0,sizeof(handles[i]));}return 0;
}
