#ifndef FLYT_CUDA_EXEC_H
#define FLYT_CUDA_EXEC_H

#include "flyt_shm_contract.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Host-private, decoded calls, NOT a payload schema or a wire structure.
 * The future adapter owns identity validation, ordered request IDs and copies
 * from untrusted shared memory. It must never pass a mapped pointer here.
 * All calls, including create/destroy, run on one dedicated session thread.
 */
#define FLYT_EXEC_MAX_ALLOCATIONS 4096u
#define FLYT_EXEC_MAX_COPY_BYTES (16u * 1024u * 1024u)
enum flyt_copy_direction { FLYT_COPY_HTOD = 1, FLYT_COPY_DTOH = 2, FLYT_COPY_DTOD = 3 };
struct flyt_device_ref { uint64_t handle; uint64_t offset; };
struct flyt_cuda_call {
    uint32_t api_id;
    union {
        int32_t device;
        uint64_t allocation_bytes;
        uint64_t free_handle;
        struct {
            uint32_t direction;
            struct flyt_device_ref dst, src;
            uint64_t bytes;
            const void *host_input;
            size_t host_input_bytes;
        } copy;
    } args;
};
struct flyt_cuda_result {
    uint32_t status, result_domain, api_result;
    int32_t device;
    uint64_t handle;
    /* Owned by result; release after adapter copies it. Empty on errors. */
    void *data;
    size_t data_bytes;
};

/* Backend callbacks use host addresses only and return raw CUDA Runtime errors.
 * Every callback is required. A zero result is success. No RPC/XDR types.
 * copy must release all dependence on host buffers before returning.
 * The context is owned by caller and must outlive the execution session.
 */
struct flyt_cuda_backend {
    uint32_t (*get_count)(void *, int32_t *);
    uint32_t (*get_device)(void *, int32_t *);
    uint32_t (*set_device)(void *, int32_t);
    uint32_t (*allocate)(void *, void **, size_t);
    uint32_t (*release)(void *, void *);
    uint32_t (*copy)(void *, void *, const void *, size_t, uint32_t);
    uint32_t (*synchronize)(void *);
};
struct flyt_cuda_exec;
/* Host-only extension handler lookup; never expose returned pointer to Guest. */
int flyt_cuda_exec_resolve(struct flyt_cuda_exec *, struct flyt_device_ref, size_t, void **);
/* One execution session per process lifetime, including non-CUDA backends.
 * Start a fresh executable process per client; do not fork after CUDA init.
 */
int flyt_cuda_exec_create(const struct flyt_cuda_backend *backend, void *context,
                          struct flyt_cuda_exec **out);
/* Result must be new/empty or previously released. No simultaneous calls.
 * Function return equals result.status. CUDA failure has status=OK and nonzero
 * api_result. Unsupported IDs never call the backend.
 */
int flyt_cuda_exec_call(struct flyt_cuda_exec *, const struct flyt_cuda_call *,
                        struct flyt_cuda_result *);
void flyt_cuda_result_release(struct flyt_cuda_result *);
/* Stops submissions first, synchronizes and releases tracked allocations.
 * On CUDA failure retains *session and returns INTERNAL_ERROR; cuda_error is
 * the raw error. Allocation/copy/free/sync errors poison the session. Destruction
 * does not retry CUDA on a poisoned session: supervisor must exit this process.
 * No cudaDeviceReset or other-session cleanup.
 */
int flyt_cuda_exec_destroy(struct flyt_cuda_exec **session, uint32_t *cuda_error);

/* CUDA backend reserves one session context per process, on the calling thread.
 * Call only AFTER the Worker HAMi/UUID/quota guard succeeds. It does not perform
 * that guard or reserve a physical GPU itself. Visible device must be only 0.
 */
struct flyt_cuda_runtime;
int flyt_cuda_runtime_open(struct flyt_cuda_runtime **out, uint32_t *cuda_error);
const struct flyt_cuda_backend *flyt_cuda_runtime_backend(void);
/* Only after exec_destroy succeeds. Process exits before starting a new client. */
void flyt_cuda_runtime_close(struct flyt_cuda_runtime *);

#ifdef __cplusplus
}
#endif
#endif
