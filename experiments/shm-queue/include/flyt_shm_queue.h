#ifndef FLYT_SHM_QUEUE_H
#define FLYT_SHM_QUEUE_H
#include "flyt_shm_contract.h"
#ifdef __cplusplus
extern "C" {
#endif

/* Local polling result only; never encode this as descriptor transport status. */
#define FLYT_SHM_AGAIN 100
#define FLYT_SHM_MAX_MESSAGE_BYTES (64u * 1024u * 1024u)
enum flyt_shm_role { FLYT_SHM_GUEST = 1, FLYT_SHM_WORKER = 2 };
struct flyt_shm_ring_layout { uint64_t offset; uint32_t entries; };
struct flyt_shm_arena { uint64_t offset, bytes; };
struct flyt_shm_slot {
    uint8_t session_id[16];
    struct flyt_shm_ring_layout request_ring, response_ring;
    struct flyt_shm_arena request_payload, response_payload;
};
/* Authoritative Host-private control-plane data, not read out of Guest memory.
 * All slots are fixed before format/open. No hot addition or same-generation
 * reattachment. Provider validates UID/epoch/binding/mapping ACK externally.
 */
struct flyt_shm_layout {
    uint8_t allocation_id[16], channel_generation[16];
    uint64_t region_bytes;
    uint32_t session_count;
    struct flyt_shm_slot slots[FLYT_SHM_MAX_SESSIONS];
};
int flyt_shm_layout_validate(const struct flyt_shm_layout *);
/* Destructive initializer for NEW, exclusively owned, unmapped-by-peers backing
 * only. Never call on a live or reused generation. Clears the entire region.
 */
int flyt_shm_format(void *mapping, size_t bytes, const struct flyt_shm_layout *);
/* Mapping is caller-owned and must remain mapped until close and peer teardown.
 * x86-64 little-endian coherent normal RAM, 64-byte aligned base, lock-free u64.
 * Both endpoints open before submissions; selected ring counters must be zero.
 * One local owner thread, no concurrent API calls; not a reconnect operation.
 * wait_ms is 1..60000 and used by the Guest receive polling helper only.
 */
int flyt_shm_open(void *mapping, size_t bytes, const struct flyt_shm_layout *,
                  uint32_t slot, enum flyt_shm_role, uint32_t wait_ms,
                  struct flyt_shm_channel **out);
void flyt_shm_close(struct flyt_shm_channel *);
/* Explicit peer failure/timeout signal from future supervisor; local only. */
void flyt_shm_abort(struct flyt_shm_channel *);

/* Guest submit/receive are declared by flyt_shm_contract.h. receive's function
 * return is delivery status; response.transport_status is the remote result.
 * try_receive is nonblocking. A too-small output buffer leaves response queued
 * and reports required output_bytes; retry receive with larger capacity, never
 * resubmit the API. Deadline is measured from publication, not from receive.
 */
int flyt_shm_try_receive(struct flyt_shm_channel *, uint64_t request_id,
                         struct flyt_shm_response *);
/* Worker takes one request into allocated private memory; release after execution.
 * No CUDA/API payload interpretation here. Unknown API/schema must be answered
 * UNSUPPORTED_API by the later execution adapter. Response capacity is available
 * before execution so the adapter can preflight output space.
 */
int flyt_shm_worker_take(struct flyt_shm_channel *, struct flyt_shm_request *);
void flyt_shm_request_release(struct flyt_shm_request *);
size_t flyt_shm_response_capacity(const struct flyt_shm_channel *);
int flyt_shm_worker_respond(struct flyt_shm_channel *,
                           const struct flyt_shm_response *);

#ifdef __cplusplus
}
#endif
#endif
