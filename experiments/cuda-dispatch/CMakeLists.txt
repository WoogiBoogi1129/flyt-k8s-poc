cmake_minimum_required(VERSION 3.18)
project(flyt_cuda_dispatch LANGUAGES C)

# Explicit opt-in only. No generated RPC files, rpcgen, XDR, or libtirpc.
# This file has not been configured or built (NOT_RUN).
find_package(Threads REQUIRED)
add_library(flyt_cuda_exec STATIC src/exec.c)
target_compile_features(flyt_cuda_exec PUBLIC c_std_11)
target_include_directories(flyt_cuda_exec PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../shm-contract/include")
target_link_libraries(flyt_cuda_exec PUBLIC Threads::Threads)
set_target_properties(flyt_cuda_exec PROPERTIES POSITION_INDEPENDENT_CODE ON)

option(FLYT_BUILD_CUDA_RUNTIME "Build the real CUDA backend (no GPU execution)" OFF)
if(FLYT_BUILD_CUDA_RUNTIME)
    find_package(CUDAToolkit 12.8 REQUIRED)
    add_library(flyt_cuda_runtime STATIC src/runtime.c)
    target_compile_features(flyt_cuda_runtime PUBLIC c_std_11)
    # Shared cudart is intentional: future Worker must preserve HAMi interception.
    target_link_libraries(flyt_cuda_runtime PUBLIC flyt_cuda_exec CUDA::cudart)
    set_target_properties(flyt_cuda_runtime PROPERTIES POSITION_INDEPENDENT_CODE ON)
endif()
