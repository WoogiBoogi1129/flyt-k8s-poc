#ifndef FLYT_TORCH_H
#define FLYT_TORCH_H
#include "flyt_wire.h"
/* Additive schema-1 extension. CUDA 12.8 / little-endian 64-bit guests only. */
enum {
    FLYT_DEVICE_PROPERTIES=0x2100, FLYT_DEVICE_ATTRIBUTE, FLYT_CUDA_VERSION,
    FLYT_ALLOCATION_ADDRESS, FLYT_MEMSET, FLYT_PRIORITY_RANGE,
    FLYT_STREAM_PRIORITY_CREATE, FLYT_PRIMARY_CONTEXT_STATE,
    FLYT_MODULE_BEGIN=0x2040, FLYT_MODULE_CHUNK, FLYT_MODULE_COMMIT,
    FLYT_FUNCTION_LAYOUT, FLYT_KERNEL_PACKED
};
#define FLYT_MAX_MODULE_BYTES (512u*1024u*1024u)
#define FLYT_MAX_PARAMETER_BYTES 32764u
#define FLYT_PROPERTY_CAPACITY 2048u
int flyt_torch_dispatch(struct flyt_cuda_session *,const struct flyt_shm_request *,struct flyt_shm_response *);
int flyt_guest_mirror_enabled(void);
#endif
