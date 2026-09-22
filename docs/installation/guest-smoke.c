#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
// ABI declarations for the intentionally narrow supported Runtime API subset.
extern int cudaGetDeviceCount(int *);
extern int cudaMalloc(void **,size_t);
extern int cudaMemcpy(void *,const void *,size_t,int);
extern int cudaDeviceSynchronize(void);
extern int cudaFree(void *);
#define CHECK(call) do {int result=(call);if(result){fprintf(stderr,"%s failed: %d\n",#call,result);return 1;}}while(0)
int main(void){
  int count=0;CHECK(cudaGetDeviceCount(&count));if(count!=1)return 2;
  unsigned char input[4096],output[4096];for(int i=0;i<4096;i++)input[i]=(unsigned char)(i*17);
  void *device=NULL;CHECK(cudaMalloc(&device,sizeof(input)));
  CHECK(cudaMemcpy(device,input,sizeof(input),1));
  CHECK(cudaDeviceSynchronize());
  CHECK(cudaMemcpy(output,device,sizeof(output),2));
  CHECK(cudaFree(device));
  if(memcmp(input,output,sizeof(input)))return 3;
  puts("PASS: Guest CUDA API -> SHM -> GPU Worker, 4096-byte round trip");
  puts("Holding the session for 20 seconds to observe Channel readiness.");
  fflush(stdout);
  sleep(20);
  return 0;
}
