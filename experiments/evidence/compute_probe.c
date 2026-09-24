/* Saturating PTX load for observed compute-cap characterization.
 * No training/tensor comparison. The host collector decides limit enforcement.
 */
#include <cuda.h>
#include <cuda_runtime_api.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
extern int flytRegisterKernelABI(CUfunction,unsigned,const unsigned char*,const unsigned char*);
#define CHECK(x) do { int e=(int)(x); if(e){fprintf(stderr,"%s failed: %d\n",#x,e);return 1;} } while(0)
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
static const char ptx[]=
 ".version 7.0\n.target sm_70\n.address_size 64\n"
 ".visible .entry load(.param .u64 ptr) {\n"
 ".reg .u64 %p,%off; .reg .u32 %i,%t,%b,%n; .reg .f32 %a; .reg .pred %q;\n"
 "ld.param.u64 %p,[ptr]; mov.u32 %t,%tid.x; mov.u32 %b,%ctaid.x; mad.lo.u32 %i,%b,256,%t;\n"
 "cvt.rn.f32.u32 %a,%i; mov.u32 %n,0;\n"
 "loop: fma.rn.f32 %a,%a,0f3f800001,0f3a83126f; add.u32 %n,%n,1; setp.lt.u32 %q,%n,1048576; @%q bra loop;\n"
 "mul.wide.u32 %off,%i,4; add.u64 %p,%p,%off; st.global.f32 [%p],%a; ret; }\n";
int main(int argc,char **argv){
 unsigned duration=argc>2?(unsigned)strtoul(argv[2],NULL,10):70;
 if(duration<30 || duration>90)return 2;
 int count=0;CHECK(cudaGetDeviceCount(&count));if(count!=1)return 3;
 void *memory=NULL;CHECK(cudaMalloc(&memory,2048*256*4));
 CUmodule module;CUfunction function;CHECK(cuModuleLoadData(&module,ptx));CHECK(cuModuleGetFunction(&function,module,"load"));
 const unsigned char sizes[]={8},pointers[]={1};CHECK(flytRegisterKernelABI(function,1,sizes,pointers));
 CUdeviceptr ptr=(CUdeviceptr)(uintptr_t)memory;void *params[]={&ptr};
 double start=now(),measure=0;unsigned warmup=0,launches=0;
 while(now()-start<duration){
  CHECK(cuLaunchKernel(function,2048,1,1,256,1,1,0,NULL,params,NULL));CHECK(cuCtxSynchronize());
  if(!measure){warmup++;if(now()-start>=10){measure=now();printf("{\"status\":\"PASS\",\"event\":\"MEASUREMENT_START\",\"warmup_seconds\":%.6f}\n",measure-start);fflush(stdout);}}
  else launches++;
 }
 double elapsed=now()-measure;
 CHECK(cuModuleUnload(module));CHECK(cudaFree(memory));
 printf("{\"status\":\"PASS\",\"scope\":\"compute load execution only, not enforcement verdict\",\"warmup_launches\":%u,\"launches\":%u,\"measurement_seconds\":%.6f,\"grid\":2048,\"block\":256}\n",warmup,launches,elapsed);
 return 0;
}
