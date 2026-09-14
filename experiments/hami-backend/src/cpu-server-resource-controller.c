/* Stage 5 dispatch. Legacy CUDA API names and RPC ABI remain unchanged. */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <cuda_runtime.h>
#include "resource-backend.h"

extern char **environ;
volatile uint32_t cur_num_sm_cores = 0;
static pthread_once_t once = PTHREAD_ONCE_INIT;
static const FlytResourceBackend *backend;
static void select_backend(void) {
    const char *name = getenv("FLYT_RESOURCE_BACKEND");
    if (name && strcmp(name, "hami") == 0) {
        for (char **item = environ; *item; ++item) {
            if (strncmp(*item, "CUDA_MPS_", 9) == 0) {
                fprintf(stderr, "HAMi backend rejects CUDA_MPS_* settings\n");
                exit(EXIT_FAILURE);
            }
        }
        backend = &flyt_hami_ops;
        return;
    }
#ifndef FLYT_HAMI_ONLY
    if (!name || strcmp(name, "mps") == 0) { backend = &flyt_mps_ops; return; }
#endif
    fprintf(stderr, "Invalid FLYT_RESOURCE_BACKEND; this image requires an explicit supported backend\n");
    exit(EXIT_FAILURE);
}
static const FlytResourceBackend *ops(void) { pthread_once(&once, select_backend); return backend; }
int flyt_hami_backend(void) { return ops() == &flyt_hami_ops; }
int change_sm_cores(uint32_t n) { return ops()->change_sm(n); }
void set_active_device(int d) { ops()->set_device(d); }
int get_active_device(void) { return ops()->get_device(); }
int set_mem_limit(uint64_t n) { return ops()->set_limit(n); }
uint64_t get_mem_limit(void) { return ops()->get_limit(); }
int allow_mem_alloc(uint64_t n) { return ops()->allow_alloc(n); }
uint64_t get_mem_free(void) { return ops()->get_free(); }
void inc_mem_usage(uint64_t n) { ops()->inc_usage(n); }
void dec_mem_usage(uint64_t n) { ops()->dec_usage(n); }
void check_and_change_resource(void) { ops()->apply_change(); }
int set_new_config(uint32_t n, uint64_t m) { return ops()->set_config(n,m); }
int init_resource_controller(uint32_t n, uint64_t m) { return ops()->init(n,m); }
void set_primary_context(void) { ops()->push_primary(); }
void unset_primary_context(void) { ops()->pop_primary(); }
void set_exec_context(void) { ops()->set_exec(); }
int flyt_backend_get_memory(uint64_t *available, uint64_t *total) {
    if (!available || !total) return cudaErrorInvalidValue;
    if (flyt_hami_backend()) return flyt_hami_memory(available, total);
    *available = get_mem_free(); *total = get_mem_limit(); return cudaSuccess;
}
