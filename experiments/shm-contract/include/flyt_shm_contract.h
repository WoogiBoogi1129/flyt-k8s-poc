#ifndef FLYT_SHM_CONTRACT_H
#define FLYT_SHM_CONTRACT_H

/* Stage 10-01 declarations only. Not wired into the runtime; NOT_RUN.
 * Wire records are byte arrays with explicit little-endian encoding. Never
 * memcpy a native C struct, pointer, size_t, enum or atomic object onto the wire.
 */
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define FLYT_SHM_ABI_MAJOR 1u
#define FLYT_SHM_ABI_MINOR 0u
#define FLYT_SHM_DESCRIPTOR_BYTES 128u
#define FLYT_SHM_CACHE_LINE_BYTES 64u
#define FLYT_SHM_MAX_SESSIONS 32u
#define FLYT_SHM_MAX_RING_ENTRIES 65536u
#define FLYT_SHM_INVALID_HANDLE UINT64_C(0)

/* Request and response use the same fixed record. All reserved bytes are zero.
 * Flags are zero in ABI 1.0. API payload schemas define IN/OUT and async behavior.
 */
enum flyt_shm_descriptor_offset {
    FLYT_D_MAGIC = 0,            /* 8 bytes: ASCII FLYTSHM1 */
    FLYT_D_MAJOR = 8,            /* u16 */
    FLYT_D_MINOR = 10,           /* u16 */
    FLYT_D_KIND = 12,            /* u16: request=1, response=2 */
    FLYT_D_FLAGS = 14,           /* u16 */
    FLYT_D_CHANNEL_GENERATION = 16, /* 16 random bytes, not a counter */
    FLYT_D_SESSION_ID = 32,      /* 16 random bytes */
    FLYT_D_REQUEST_ID = 48,      /* u64, starts at 1; never reused in session */
    FLYT_D_API_ID = 56,          /* u32 */
    FLYT_D_PAYLOAD_SCHEMA = 60,  /* u32 */
    FLYT_D_PAYLOAD_OFFSET = 64,  /* u64 from session payload arena base */
    FLYT_D_PAYLOAD_LENGTH = 72,  /* u64 */
    FLYT_D_TRANSPORT_STATUS = 80,/* u32, zero in request */
    FLYT_D_RESULT_DOMAIN = 84,   /* u32: none/runtime/driver/library */
    FLYT_D_API_RESULT = 88,      /* u32 raw API return bits; NOT an errno */
    FLYT_D_COMPLETION = 92,      /* u32: 0=request, 1=API return */
    FLYT_D_RESERVED = 96        /* 32 zero bytes */
};

enum flyt_shm_status {
    FLYT_SHM_OK = 0,
    FLYT_SHM_UNSUPPORTED_API = 1,
    FLYT_SHM_BAD_VERSION = 2,
    FLYT_SHM_BAD_DESCRIPTOR = 3,
    FLYT_SHM_STALE_IDENTITY = 4,
    FLYT_SHM_QUEUE_FULL = 5,     /* Local submit failure: request NOT published. */
    FLYT_SHM_CHANNEL_CLOSED = 6,
    FLYT_SHM_EXECUTION_UNKNOWN = 7, /* No automatic retry. */
    FLYT_SHM_INTERNAL_ERROR = 8
};
enum flyt_shm_result_domain {
    FLYT_RESULT_NONE = 0, FLYT_RESULT_CUDA_RUNTIME = 1,
    FLYT_RESULT_CUDA_DRIVER = 2, FLYT_RESULT_LIBRARY = 3
};

/* API ID allocation is provisional but IDs are never repurposed within major 1.
 * Listing an API here does not implement it. Stage 10-06 defines payload schemas.
 */
enum flyt_shm_api_id {
    FLYT_API_RUNTIME_GET_DEVICE_COUNT = 0x1001,
    FLYT_API_RUNTIME_GET_DEVICE = 0x1002,
    FLYT_API_RUNTIME_SET_DEVICE = 0x1003,
    FLYT_API_RUNTIME_MALLOC = 0x1010,
    FLYT_API_RUNTIME_FREE = 0x1011,
    FLYT_API_RUNTIME_MEMCPY = 0x1012,
    FLYT_API_RUNTIME_MEM_GET_INFO = 0x1013,
    FLYT_API_RUNTIME_DEVICE_SYNCHRONIZE = 0x1020
};

/* Host-local decoded objects, never mapped as wire structs. */
struct flyt_shm_identity {
    uint8_t channel_generation[16];
    uint8_t session_id[16];
};
struct flyt_shm_request {
    struct flyt_shm_identity identity;
    uint64_t request_id;
    uint32_t api_id;
    uint32_t payload_schema;
    const uint8_t *input;
    size_t input_bytes;
};
struct flyt_shm_response {
    uint32_t transport_status;
    uint32_t result_domain;
    uint32_t api_result;
    uint8_t *output;
    size_t output_capacity;
    size_t output_bytes;
};
struct flyt_cuda_session;
struct flyt_shm_channel;

/* Implemented in later branches. No RPC fallback is permitted. The dispatcher
 * snapshots and validates input before CUDA execution; output belongs to caller.
 */
int flyt_cuda_dispatch(struct flyt_cuda_session *session,
                      const struct flyt_shm_request *request,
                      struct flyt_shm_response *response);
int flyt_shm_submit(struct flyt_shm_channel *channel,
                    const struct flyt_shm_request *request);
int flyt_shm_receive(struct flyt_shm_channel *channel, uint64_t request_id,
                     struct flyt_shm_response *response);

#ifdef __cplusplus
}
#endif
#endif
