/* Native feasibility check, not VM evidence. Device addresses are reserved in
 * a separate CPU process by the SHM Guest; this probe checks their alignment
 * and the exact parameter layouts reported by CUDA for compiled kernels. */
#include <cuda_runtime.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>

struct Payload { float *base; float *inside; uint64_t scalar; float add; int pad[9]; };
__global__ void nested(Payload p, uint64_t untouched) {
    if (threadIdx.x == 0) {
        *p.inside = p.base[0] + p.add;
        p.base[2] = (p.scalar == untouched) ? 7.0f : -7.0f;
    }
}
#define CHECK(x) do { cudaError_t e=(x); if(e!=cudaSuccess) { \
    fprintf(stderr,"%s: %s\n",#x,cudaGetErrorString(e)); return 1; } } while(0)
int main() {
    float *device; CHECK(cudaMalloc(&device,4096));
    float values[3]={2,0,0}; CHECK(cudaMemcpy(device,values,sizeof(values),cudaMemcpyHostToDevice));
    Payload p={device,device+1,static_cast<uint64_t>(reinterpret_cast<uintptr_t>(device)),3,{}};
    size_t off=0,bytes=0;
    if(!getenv("FLYT_MIRROR_DEVICE_VA")) {
    CHECK(cudaFuncGetParamInfo((const void*)nested,0,&off,&bytes));
    printf("{\"parameter\":0,\"offset\":%zu,\"bytes\":%zu,\"expected_bytes\":%zu}\n",off,bytes,sizeof(p));
    if(bytes!=sizeof(p)||bytes<=16) return 2;
    CHECK(cudaFuncGetParamInfo((const void*)nested,1,&off,&bytes));
    printf("{\"parameter\":1,\"offset\":%zu,\"bytes\":%zu}\n",off,bytes);
    }
    uint64_t scalar=p.scalar; void *args[]={&p,&scalar};
    CHECK(cudaLaunchKernel((const void*)nested,dim3(1),dim3(1),args,0,0));
    CHECK(cudaMemcpy(values,device,sizeof(values),cudaMemcpyDeviceToHost));
    bool ok=values[0]==2&&values[1]==5&&values[2]==7;
    printf("{\"status\":\"%s\",\"scope\":\"%s\",\"page_aligned\":%s,\"address\":%llu}\n",
           ok?"PASS":"FAIL",getenv("FLYT_MIRROR_DEVICE_VA")?"SHM compiled kernel; route requires binding evidence":"native ABI feasibility only",reinterpret_cast<uintptr_t>(device)%4096==0?"true":"false",
           (unsigned long long)reinterpret_cast<uintptr_t>(device));
    CHECK(cudaFree(device)); return ok?0:1;
}
