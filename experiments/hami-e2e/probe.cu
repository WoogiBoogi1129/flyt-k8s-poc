// Explicit experiment executable. No GPU operation runs during image creation.
#include <cuda.h>
#include <cuda_runtime.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>
#include "burn.cuh"
using Clock = std::chrono::steady_clock;
constexpr size_t MiB = 1024ULL * 1024;
static std::string run_id, api, mode;
static bool driver, async_alloc;
static void require(bool ok, const char *msg) { if (!ok) throw std::runtime_error(msg); }
static void runtime_ok(cudaError_t e) { require(e == cudaSuccess, "CUDA runtime operation failed"); }
static void driver_ok(CUresult e) { require(e == CUDA_SUCCESS, "CUDA driver operation failed"); }
static void emit(const std::string &fields) {
    std::cout << "FLYT_E2E {\"schema\":1,\"run_id\":\"" << run_id
              << "\",\"api\":\"" << api << "\",\"mode\":\"" << mode << "\"," << fields << "}" << std::endl;
}
static void gate(const char *expected) {
    std::string line; require(bool(std::getline(std::cin,line)) && line == expected, "experiment gate closed");
}
static void sync_device() { if (driver) driver_ok(cuCtxSynchronize()); else runtime_ok(cudaDeviceSynchronize()); }
static int allocate(void **p, size_t n) {
    *p = nullptr;
    if (driver) { CUdeviceptr d = 0; auto e = cuMemAlloc(&d,n); *p = reinterpret_cast<void *>(d); return int(e); }
    return int(async_alloc ? cudaMallocAsync(p,n,0) : cudaMalloc(p,n));
}
static void release(void *p) {
    if (driver) driver_ok(cuMemFree(reinterpret_cast<CUdeviceptr>(p)));
    else runtime_ok(async_alloc ? cudaFreeAsync(p,0) : cudaFree(p));
}
static void touch(void *p, size_t n) {
    if (driver) driver_ok(cuMemsetD8(reinterpret_cast<CUdeviceptr>(p),0x5a,n));
    else runtime_ok(cudaMemset(p,0x5a,n));
    sync_device();
}
static int oom_code() { return driver ? int(CUDA_ERROR_OUT_OF_MEMORY) : int(cudaErrorMemoryAllocation); }
static long number(const std::map<std::string,std::string> &a, const char *name, long low, long high) {
    const auto &s = a.at(name); require(!s.empty() && s.find_first_not_of("0123456789") == std::string::npos,"invalid numeric input");
    auto n = std::stol(s); require(n >= low && n <= high,"numeric input out of range"); return n;
}
int main(int argc, char **argv) {
    bool ready = false;
    try {
        require(argc % 2 == 1, "expected --name value pairs");
        std::map<std::string,std::string> a;
        for (int i=1; i<argc; i+=2) require(a.emplace(argv[i],argv[i+1]).second,"duplicate argument");
        run_id=a.at("--run-id"); api=a.at("--api"); mode=a.at("--mode");
        require(run_id.size()==32 && run_id.find_first_not_of("0123456789abcdef")==std::string::npos,"invalid run id");
        require(api=="runtime" || api=="driver" || api=="async","unknown API");
        require(mode=="smoke" || mode=="memory" || mode=="hold" || mode=="compute","unknown mode");
        driver=api=="driver"; async_alloc=api=="async";
        long quota=number(a,"--quota-mib",256,1048576), chunk=number(a,"--chunk-mib",1,1024);
        long headroom=number(a,"--headroom-mib",1,quota/2), peer=number(a,"--peer-mib",0,quota-1);
        long seconds=number(a,"--seconds",5,120), hold=number(a,"--hold-mib",0,quota-1);
        require(chunk<=headroom && peer+headroom<quota,"invalid quota margin");
        bool guest=a.at("--role")=="guest";
        require(guest || a.at("--role")=="standalone","unknown role");
        require(!guest || access("/dev/nvidiactl",F_OK)!=0,"guest has direct NVIDIA access");
        std::ifstream maps("/proc/self/maps"); std::string line; bool client=false,hami=false;
        require(bool(maps),"cannot inspect mappings");
        while(std::getline(maps,line)) {client |= line.find("cricket-client")!=std::string::npos; hami |= line.find("libvgpu.so")!=std::string::npos;}
        require(guest ? (client && !hami) : (hami && !client),"unexpected client/HAMi mapping");
        extern char **environ;
        for(char **e=environ; *e; ++e) require(std::string(*e).rfind("CUDA_MPS_",0)!=0,"MPS setting present");
        CUcontext ctx=nullptr; CUmodule module=nullptr; CUfunction function=nullptr;
        unsigned char bytes[16]; int count=0;
        if(driver) {
            driver_ok(cuInit(0)); driver_ok(cuDeviceGetCount(&count)); require(count==1,"expected one device");
            CUdevice dev; CUuuid uuid; driver_ok(cuDeviceGet(&dev,0)); driver_ok(cuDeviceGetUuid(&uuid,dev));
            std::copy(uuid.bytes,uuid.bytes+16,bytes); driver_ok(cuDevicePrimaryCtxRetain(&ctx,dev)); driver_ok(cuCtxSetCurrent(ctx));
        } else {
            runtime_ok(cudaGetDeviceCount(&count)); require(count==1,"expected one device");
            cudaDeviceProp p{}; runtime_ok(cudaGetDeviceProperties(&p,0)); std::copy(p.uuid.bytes,p.uuid.bytes+16,bytes);
            runtime_ok(cudaSetDevice(0)); runtime_ok(cudaFree(nullptr));
        }
        char uuid[41]; std::snprintf(uuid,sizeof(uuid),"GPU-%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
            bytes[0],bytes[1],bytes[2],bytes[3],bytes[4],bytes[5],bytes[6],bytes[7],bytes[8],bytes[9],bytes[10],bytes[11],bytes[12],bytes[13],bytes[14],bytes[15]);
        require(a.at("--uuid")==uuid,"GPU UUID mismatch");
        emit("\"event\":\"READY\",\"pid\":"+std::to_string(getpid())+",\"gpu_uuid\":\""+uuid+"\",\"role\":\""+a.at("--role")+"\"");
        ready=true; gate("GO");
        size_t available=0,total=0;
        if(driver) driver_ok(cuMemGetInfo(&available,&total)); else runtime_ok(cudaMemGetInfo(&available,&total));
        require(total>0 && total<=size_t(quota)*MiB && available<=total,"memory observation inconsistent with quota");
        emit("\"event\":\"MEMORY_INFO\",\"free_bytes\":"+std::to_string(available)+",\"total_bytes\":"+std::to_string(total));
        bool pass=true;
        if(mode=="memory") {
            std::vector<void*> owned; long allocated=0; int error=0;
            // Bounded even if HAMi is bypassed: stop after the first quota-crossing chunk.
            while(allocated<=quota-peer) {
                void *p=nullptr; error=allocate(&p,size_t(chunk)*MiB); if(error) break;
                owned.push_back(p); allocated+=chunk; touch(p,size_t(chunk)*MiB);
            }
            pass=error==oom_code() && allocated>=quota-peer-headroom && allocated<=quota-peer;
            for(void *p:owned) release(p); sync_device();
            void *reused=nullptr; int reuse_error=allocate(&reused,size_t(chunk)*MiB);
            if(!reuse_error) {touch(reused,size_t(chunk)*MiB); release(reused); sync_device();}
            pass &= reuse_error==0;
            emit("\"event\":\"BOUNDARY\",\"allocated_mib\":"+std::to_string(allocated)+",\"peer_mib\":"+std::to_string(peer)+
                ",\"cuda_error\":"+std::to_string(error)+",\"reuse_error\":"+std::to_string(reuse_error));
        } else if(mode=="hold") {
            require(hold>0 && hold+headroom<quota,"invalid hold size"); void *p=nullptr;
            require(allocate(&p,size_t(hold)*MiB)==0,"peer allocation failed"); touch(p,size_t(hold)*MiB);
            emit("\"event\":\"HELD\",\"held_mib\":"+std::to_string(hold)); gate("RELEASE"); release(p); sync_device();
        } else {
            constexpr int blocks=1024, threads=256; void *p=nullptr;
            require(allocate(&p,blocks*threads*sizeof(float))==0,"workload allocation failed");
            if(driver) {driver_ok(cuModuleLoad(&module,a.at("--ptx").c_str())); driver_ok(cuModuleGetFunction(&function,module,"flyt_e2e_burn"));}
            auto launch=[&](){
                if(driver) {CUdeviceptr dp=reinterpret_cast<CUdeviceptr>(p); void *args[]={&dp}; driver_ok(cuLaunchKernel(function,blocks,1,1,threads,1,1,0,nullptr,args,nullptr));}
                else {flyt_e2e_burn<<<blocks,threads>>>(static_cast<float*>(p)); runtime_ok(cudaGetLastError());}
                sync_device();
            };
            unsigned long long launches=0; double elapsed=0;
            if(mode=="compute") {
                auto warm=Clock::now(); do {launch();} while(std::chrono::duration<double>(Clock::now()-warm).count()<3);
                auto start=Clock::now(); do {launch(); ++launches;} while(std::chrono::duration<double>(Clock::now()-start).count()<seconds);
                elapsed=std::chrono::duration<double>(Clock::now()-start).count();
            } else {launch(); launches=1;}
            std::vector<float> host(blocks*threads);
            if(driver) driver_ok(cuMemcpyDtoH(host.data(),reinterpret_cast<CUdeviceptr>(p),host.size()*sizeof(float)));
            else runtime_ok(cudaMemcpy(host.data(),p,host.size()*sizeof(float),cudaMemcpyDeviceToHost));
            // Independent CPU calculation catches wrong results, not just finite output.
            float reference[31]; for(int i=0;i<31;++i) {float x=0.1f+float(i)*0.001f; for(int j=0;j<8192;++j)x=std::fma(x,0.999999f,0.000001f);reference[i]=x;}
            for(size_t i=0;i<host.size();++i) pass &= std::isfinite(host[i]) && std::abs(host[i]-reference[i%31])<0.0001f;
            emit("\"event\":\"WORKLOAD\",\"launches\":"+std::to_string(launches)+",\"elapsed_seconds\":"+std::to_string(elapsed)+
                ",\"launches_per_second\":"+std::to_string(elapsed>0?launches/elapsed:0));
            release(p); sync_device(); if(module) driver_ok(cuModuleUnload(module));
        }
        if(driver && ctx) driver_ok(cuDevicePrimaryCtxRelease(0));
        emit(std::string("\"event\":\"RESULT\",\"status\":\"")+(pass?"PASS":"FAIL")+"\""); return pass?0:1;
    } catch(const std::exception &e) {
        // Fixed error descriptions only; no arbitrary environment or CUDA payload dumps.
        std::cerr << "probe error: " << e.what() << std::endl;
        emit(std::string("\"event\":\"RESULT\",\"status\":\"")+(ready?"FAIL":"BLOCKED")+"\""); return ready?1:2;
    }
}
