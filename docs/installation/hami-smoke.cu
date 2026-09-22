#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>

#define CHECK(call) do { cudaError_t e=(call); if(e!=cudaSuccess) { \
  fprintf(stderr,"%s: %s\n",#call,cudaGetErrorString(e)); return 1; }} while(0)
__global__ void add(int *out) { out[0]=19+23; }
int main(void) {
  int count=0,answer=0; CHECK(cudaGetDeviceCount(&count));
  if(count!=1) { fprintf(stderr,"Expected one allocated GPU, got %d\n",count); return 1; }
  size_t free_bytes=0,total_bytes=0;
  CHECK(cudaMemGetInfo(&free_bytes,&total_bytes));
  printf("GPU_COUNT=%d FREE_BYTES=%zu TOTAL_BYTES=%zu\n",count,free_bytes,total_bytes);
  int *d=NULL; CHECK(cudaMalloc(&d,sizeof(int))); add<<<1,1>>>(d);
  CHECK(cudaGetLastError()); CHECK(cudaDeviceSynchronize());
  CHECK(cudaMemcpy(&answer,d,sizeof(int),cudaMemcpyDeviceToHost)); CHECK(cudaFree(d));
  if(answer!=42) return 2;
  printf("CUDA_RESULT=%d\n",answer);
  // The Pod requests 1024 MiB. A 1536 MiB allocation must fail.
  void *large=NULL; cudaError_t e=cudaMalloc(&large,1536ULL*1024*1024);
  printf("OVER_QUOTA_ALLOCATION=%s\n",cudaGetErrorString(e));
  if(e==cudaSuccess) { cudaFree(large); return 3; }
  if(e!=cudaErrorMemoryAllocation) return 4;
  puts("PASS: CUDA computation and memory quota enforcement");
  return 0;
}
