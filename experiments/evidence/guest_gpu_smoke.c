#include <cuda.h>
#include <cuda_runtime_api.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>

extern int flytRegisterKernelABI(CUfunction,unsigned,const unsigned char*,const unsigned char*);
#define CHECK(x) do { int e=(int)(x); if(e){fprintf(stderr,"%s failed: %d\n",#x,e);return 1;} } while(0)
static const char ptx[]=
    ".version 7.0\n.target sm_70\n.address_size 64\n"
    ".visible .entry write_value(.param .u64 ptr, .param .u32 seed) {\n"
    ".reg .u64 %rd; .reg .u32 %r;\n"
    "ld.param.u64 %rd, [ptr]; ld.param.u32 %r, [seed];\n"
    "add.u32 %r, %r, 19; st.global.u32 [%rd], %r; ret; }\n";

int main(int argc,char **argv){
    unsigned seed=argc>1?(unsigned)strtoul(argv[1],NULL,10):23;
    unsigned hold=argc>2?(unsigned)strtoul(argv[2],NULL,10):10;
    int count=0;CHECK(cudaGetDeviceCount(&count));if(count!=1)return 2;
    unsigned char src[4096],dst[4096];for(int i=0;i<4096;i++)src[i]=(unsigned char)(seed+i*17);
    void *memory=NULL;CHECK(cudaMalloc(&memory,sizeof(src)));
    CHECK(cudaMemcpy(memory,src,sizeof(src),cudaMemcpyHostToDevice));
    CHECK(cudaMemcpy(dst,memory,sizeof(dst),cudaMemcpyDeviceToHost));
    if(memcmp(src,dst,sizeof(src)))return 3;
    CUmodule module;CUfunction function;CHECK(cuModuleLoadData(&module,ptx));
    CHECK(cuModuleGetFunction(&function,module,"write_value"));
    const unsigned char sizes[]={8,4},pointers[]={1,0};CHECK(flytRegisterKernelABI(function,2,sizes,pointers));
    CUdeviceptr ptr=(CUdeviceptr)(uintptr_t)memory;
    struct timespec started,now;clock_gettime(CLOCK_MONOTONIC,&started);
    unsigned repeats=0;
    do {
        unsigned i=repeats;
        unsigned input=seed+i,actual=0;void *params[]={&ptr,&input};
        CHECK(cuLaunchKernel(function,1,1,1,1,1,1,0,NULL,params,NULL));
        CHECK(cuCtxSynchronize());
        CHECK(cudaMemcpy(&actual,memory,sizeof(actual),cudaMemcpyDeviceToHost));
        if(actual!=input+19){fprintf(stderr,"kernel mismatch %u != %u\n",actual,input+19);return 4;}
        repeats++;clock_gettime(CLOCK_MONOTONIC,&now);
    } while(repeats<100 || (now.tv_sec-started.tv_sec)+(now.tv_nsec-started.tv_nsec)/1e9<hold);
    CHECK(cuModuleUnload(module));CHECK(cudaFree(memory));
    printf("{\"status\":\"PASS\",\"scope\":\"Guest-SHM-HAMi-GPU memory and PTX kernel\",\"seed\":%u,\"kernel_repeats\":%u,\"copy_bytes\":4096,\"active_seconds\":%.6f}\n",seed,repeats,(now.tv_sec-started.tv_sec)+(now.tv_nsec-started.tv_nsec)/1e9);
    fflush(stdout);return 0;
}
