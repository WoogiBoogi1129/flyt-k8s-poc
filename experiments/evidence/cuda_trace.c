/* Development-only CUPTI trace. Never preload into measured performance runs.
 * Records real Runtime/Driver calls, including calls through entry-point APIs.
 * No CUDA calls are made inside the callback. */
#define _GNU_SOURCE
#include <cupti.h>
#include <cuda_runtime_api.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static CUpti_SubscriberHandle subscriber;
static FILE *output;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned long long sequence;
struct kernel { const void *host; const char *name; struct kernel *next; };
static struct kernel *kernels;

static void json_string(const char *s) {
    fputc('"', output);
    for (; s && *s; ++s) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') fprintf(output, "\\%c", c);
        else if (c < 32) fprintf(output, "\\u%04x", c);
        else fputc(c, output);
    }
    fputc('"', output);
}

static void CUPTIAPI callback(void *unused, CUpti_CallbackDomain domain,
                             CUpti_CallbackId id, const void *data) {
    (void)unused; (void)id;
    const CUpti_CallbackData *call = data;
    if (!output || (domain != CUPTI_CB_DOMAIN_RUNTIME_API &&
                    domain != CUPTI_CB_DOMAIN_DRIVER_API)) return;
    pthread_mutex_lock(&lock);
    fprintf(output, "{\"sequence\":%llu,\"domain\":\"%s\",\"site\":\"%s\",\"api\":",
            ++sequence, domain == CUPTI_CB_DOMAIN_RUNTIME_API ? "runtime" : "driver",
            call->callbackSite == CUPTI_API_ENTER ? "enter" : "exit");
    json_string(call->functionName);
    /* The installed driver/CUPTI combination produced an invalid symbolName
     * on an autograd-thread launch. Get names from actual Runtime registration
     * below; never dereference that optional callback field. */
    fputs(",\"kernel\":\"\"", output);
    fprintf(output, ",\"correlation_id\":%u}\n", call->correlationId);
    pthread_mutex_unlock(&lock);
}

void __cudaRegisterFunction(void **fat, const char *host, char *device,
    const char *name, int limit, void *tid, void *bid, void *bd, void *gd, int *ws) {
    typedef void (*fn)(void**,const char*,char*,const char*,int,void*,void*,void*,void*,int*);
    fn real=(fn)dlsym(RTLD_NEXT,"__cudaRegisterFunction");
    if(!real) _exit(125);
    struct kernel *k=malloc(sizeof(*k));
    if(!k) _exit(125);
    k->host=host;k->name=strdup(name);
    pthread_mutex_lock(&lock);k->next=kernels;kernels=k;pthread_mutex_unlock(&lock);
    real(fat,host,device,name,limit,tid,bid,bd,gd,ws);
}

cudaError_t cudaLaunchKernel(const void *function, struct dim3 grid, struct dim3 block,
                            void **args, size_t shared, cudaStream_t stream) {
    typedef cudaError_t (*fn)(const void*,struct dim3,struct dim3,void**,size_t,cudaStream_t);
    fn real=(fn)dlsym(RTLD_NEXT,"cudaLaunchKernel");
    if(!real) _exit(125);
    size_t offsets[64],sizes[64],count=0;
    /* End-of-parameter discovery reports invalid-value and sets last-error.
     * Do not leak that diagnostic query error into PyTorch's launch checks. */
    cudaError_t before=cudaPeekAtLastError();
    if(before==cudaSuccess){
        while(count<64 && cudaFuncGetParamInfo(function,count,&offsets[count],&sizes[count])==cudaSuccess) ++count;
        cudaGetLastError();
    }
    if(output) {
        pthread_mutex_lock(&lock);
        struct kernel *k=kernels;while(k&&k->host!=function)k=k->next;
        fprintf(output,"{\"sequence\":%llu,\"domain\":\"kernel_layout\",\"site\":\"metadata\",\"api\":\"cudaLaunchKernel\",\"kernel\":",++sequence);
        json_string(k?k->name:NULL);fputs(",\"parameters\":[",output);
        for(size_t i=0;i<count;++i)fprintf(output,"%s{\"offset\":%zu,\"bytes\":%zu}",i?",":"",offsets[i],sizes[i]);
        fputs("]}\n",output);pthread_mutex_unlock(&lock);
    }
    return real(function,grid,block,args,shared,stream);
}

__attribute__((constructor)) static void start_trace(void) {
    const char *path = getenv("FLYT_CUDA_TRACE");
    if (!path) return;
    output = fopen(path, "wx");
    if (!output) { perror("FLYT_CUDA_TRACE"); _exit(125); }
    setvbuf(output, NULL, _IOLBF, 0);
    CUptiResult e = cuptiSubscribe(&subscriber, callback, NULL);
    if (e == CUPTI_SUCCESS) e = cuptiEnableDomain(1, subscriber, CUPTI_CB_DOMAIN_RUNTIME_API);
    if (e == CUPTI_SUCCESS) e = cuptiEnableDomain(1, subscriber, CUPTI_CB_DOMAIN_DRIVER_API);
    if (e != CUPTI_SUCCESS) {
        fprintf(stderr, "CUPTI trace initialization failed: %d\n", (int)e);
        _exit(125);
    }
}

__attribute__((destructor)) static void stop_trace(void) {
    if (!output) return;
    cuptiUnsubscribe(subscriber);
    fclose(output); output = NULL;
}
