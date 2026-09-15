cmake_minimum_required(VERSION 3.18)
project(flyt_shm_queue LANGUAGES C)
# Authored only: configure/build/link/tests NOT_RUN. No CUDA or RPC dependency.
find_package(Threads REQUIRED)
add_library(flyt_shm_queue STATIC src/queue.c)
target_compile_features(flyt_shm_queue PUBLIC c_std_11)
target_include_directories(flyt_shm_queue PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/include"
    "${CMAKE_CURRENT_SOURCE_DIR}/../shm-contract/include")
target_link_libraries(flyt_shm_queue PUBLIC Threads::Threads)
set_target_properties(flyt_shm_queue PROPERTIES POSITION_INDEPENDENT_CODE ON)
