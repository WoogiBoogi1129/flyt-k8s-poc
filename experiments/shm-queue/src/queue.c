#define _POSIX_C_SOURCE 200809L
#include "flyt_shm_queue.h"
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>

struct ring {
    uint8_t *base;
    uint32_t entries;
    uint64_t local, peer; /* private shadow counters, never trusted from peer */
};
struct flyt_shm_channel {
    struct ring req, resp;
    uint8_t *request_payload, *response_payload;
    size_t request_bytes, response_bytes;
    struct flyt_shm_identity identity;
    pthread_t owner;
    enum flyt_shm_role role;
    int failed, pending;
    uint32_t wait_ms, api, schema;
    uint64_t next, active, deadline_ns;
};
struct record {
    uint16_t kind;
    struct flyt_shm_identity identity;
    uint64_t id, offset, bytes;
    uint32_t api, schema, status, domain, result;
};
static uint64_t get(const uint8_t *p, unsigned n)
{
    uint64_t v = 0;
    unsigned i;
    for (i = 0; i < n; ++i) v |= (uint64_t)p[i] << (i * 8);
    return v;
}
static void put(uint8_t *p, uint64_t v, unsigned n)
{
    unsigned i;
    for (i = 0; i < n; ++i) p[i] = (uint8_t)(v >> (i * 8));
}
static int nonzero(const uint8_t *p, size_t n)
{
    size_t i;
    for (i = 0; i < n; ++i) if (p[i]) return 1;
    return 0;
}
/* External shared memory can change at any time. Only private snapshots feed
 * validation/decoding; volatile byte loads avoid rereading fields from mapping.
 */
static void snapshot(void *dst, const void *src, size_t n)
{
    uint8_t *d = dst;
    const volatile uint8_t *s = src;
    size_t i;
    for (i = 0; i < n; ++i) d[i] = s[i];
}
static uint64_t acquire(uint8_t *p)
{ return __atomic_load_n((uint64_t *)(void *)p, __ATOMIC_ACQUIRE); }
static void publish(uint8_t *p, uint64_t v)
{ __atomic_store_n((uint64_t *)(void *)p, v, __ATOMIC_RELEASE); }

int flyt_shm_layout_validate(const struct flyt_shm_layout *l)
{
    uint64_t offsets[128], sizes[128];
    unsigned i, j, k = 0;
    if (!l || l->region_bytes < 1048576 || l->region_bytes > UINT64_C(4294967296) ||
        l->region_bytes % 4096 || l->region_bytes > SIZE_MAX ||
        !l->session_count || l->session_count > FLYT_SHM_MAX_SESSIONS ||
        !nonzero(l->allocation_id, 16) || !nonzero(l->channel_generation, 16))
        return FLYT_SHM_BAD_DESCRIPTOR;
    for (i = 0; i < l->session_count; ++i) {
        const struct flyt_shm_slot *s = &l->slots[i];
        struct flyt_shm_ring_layout rings[2] = {s->request_ring, s->response_ring};
        struct flyt_shm_arena arenas[2] = {s->request_payload, s->response_payload};
        if (!nonzero(s->session_id, 16)) return FLYT_SHM_BAD_DESCRIPTOR;
        for (j = 0; j < i; ++j)
            if (!memcmp(s->session_id, l->slots[j].session_id, 16))
                return FLYT_SHM_BAD_DESCRIPTOR;
        for (j = 0; j < 2; ++j) {
            uint32_t n = rings[j].entries;
            if (n < 64 || n > FLYT_SHM_MAX_RING_ENTRIES || (n & (n - 1)))
                return FLYT_SHM_BAD_DESCRIPTOR;
            offsets[k] = rings[j].offset;
            sizes[k++] = 128 + (uint64_t)n * 128;
            if (arenas[j].bytes < 64 || arenas[j].bytes % 64)
                return FLYT_SHM_BAD_DESCRIPTOR;
            offsets[k] = arenas[j].offset;
            sizes[k++] = arenas[j].bytes;
        }
    }
    for (i = 0; i < k; ++i) {
        if (offsets[i] < 4096 || offsets[i] % 64 || offsets[i] > l->region_bytes ||
            sizes[i] > l->region_bytes - offsets[i]) return FLYT_SHM_BAD_DESCRIPTOR;
        for (j = 0; j < i; ++j)
            if (offsets[i] < offsets[j] + sizes[j] && offsets[j] < offsets[i] + sizes[i])
                return FLYT_SHM_BAD_DESCRIPTOR;
    }
    return FLYT_SHM_OK;
}
static int mapping_ok(void *p, size_t bytes, const struct flyt_shm_layout *l)
{
#if !defined(__x86_64__) || !defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
    (void)p; (void)bytes; (void)l;
    return FLYT_SHM_BAD_VERSION;
#else
    int rc = flyt_shm_layout_validate(l);
    if (rc) return rc;
    if (!p || (uintptr_t)p % 64 || bytes != l->region_bytes ||
        !__atomic_always_lock_free(8, 0)) return FLYT_SHM_BAD_DESCRIPTOR;
    return FLYT_SHM_OK;
#endif
}
int flyt_shm_format(void *p, size_t bytes, const struct flyt_shm_layout *l)
{
    uint8_t *b = p;
    int rc = mapping_ok(p, bytes, l);
    if (rc) return rc;
    memset(p, 0, bytes);
    memcpy(b, "FLYTCHN1", 8);
    put(b + 8, 1, 2); put(b + 10, 0, 2); put(b + 12, 4096, 4);
    memcpy(b + 16, l->allocation_id, 16);
    memcpy(b + 32, l->channel_generation, 16);
    put(b + 48, bytes, 8); put(b + 56, l->session_count, 4);
    /* Provider publishes readiness out-of-band AFTER formatting completes. */
    return FLYT_SHM_OK;
}
int flyt_shm_open(void *p, size_t bytes, const struct flyt_shm_layout *l,
                  uint32_t slot, enum flyt_shm_role role, uint32_t wait_ms,
                  struct flyt_shm_channel **out)
{
    uint8_t h[4096], *b = p;
    struct flyt_shm_channel *c;
    const struct flyt_shm_slot *s;
    int rc;
    if (!out) return FLYT_SHM_BAD_DESCRIPTOR;
    *out = NULL;
    rc = mapping_ok(p, bytes, l);
    if (rc) return rc;
    if (slot >= l->session_count || (role != FLYT_SHM_GUEST && role != FLYT_SHM_WORKER) ||
        !wait_ms || wait_ms > 60000) return FLYT_SHM_BAD_DESCRIPTOR;
    snapshot(h, b, sizeof(h));
    if (memcmp(h, "FLYTCHN1", 8) || get(h + 8, 2) != 1 || get(h + 10, 2))
        return FLYT_SHM_BAD_VERSION;
    if (get(h + 12, 4) != 4096 || get(h + 48, 8) != bytes ||
        get(h + 56, 4) != l->session_count || nonzero(h + 60, 4036))
        return FLYT_SHM_BAD_DESCRIPTOR;
    if (memcmp(h + 16, l->allocation_id, 16) || memcmp(h + 32, l->channel_generation, 16))
        return FLYT_SHM_STALE_IDENTITY;
    s = &l->slots[slot];
    if (acquire(b + s->request_ring.offset) || acquire(b + s->request_ring.offset + 64) ||
        acquire(b + s->response_ring.offset) || acquire(b + s->response_ring.offset + 64))
        return FLYT_SHM_BAD_DESCRIPTOR;
    c = calloc(1, sizeof(*c));
    if (!c) return FLYT_SHM_INTERNAL_ERROR;
    c->req.base = b + s->request_ring.offset; c->req.entries = s->request_ring.entries;
    c->resp.base = b + s->response_ring.offset; c->resp.entries = s->response_ring.entries;
    c->request_payload = b + s->request_payload.offset;
    c->request_bytes = (size_t)s->request_payload.bytes;
    c->response_payload = b + s->response_payload.offset;
    c->response_bytes = (size_t)s->response_payload.bytes;
    memcpy(c->identity.channel_generation, l->channel_generation, 16);
    memcpy(c->identity.session_id, s->session_id, 16);
    c->owner = pthread_self(); c->role = role; c->wait_ms = wait_ms; c->next = 1;
    *out = c;
    return FLYT_SHM_OK;
}
static int usable(struct flyt_shm_channel *c, enum flyt_shm_role role)
{
    if (!c || c->role != role || !pthread_equal(c->owner, pthread_self()))
        return FLYT_SHM_BAD_DESCRIPTOR;
    if (c->failed) return c->pending ? FLYT_SHM_EXECUTION_UNKNOWN : FLYT_SHM_CHANNEL_CLOSED;
    return FLYT_SHM_OK;
}
static int fail(struct flyt_shm_channel *c, int rc) { c->failed = 1; return rc; }
static int ticks(uint64_t *out)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) || t.tv_sec < 0 ||
        (uint64_t)t.tv_sec > (UINT64_MAX - (uint64_t)t.tv_nsec) / UINT64_C(1000000000))
        return FLYT_SHM_INTERNAL_ERROR;
    *out = (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
    return 0;
}
static void encode(uint8_t *b, const struct record *r)
{
    memset(b, 0, 128); memcpy(b, "FLYTSHM1", 8);
    put(b + 8, 1, 2); put(b + 12, r->kind, 2);
    memcpy(b + 16, r->identity.channel_generation, 16);
    memcpy(b + 32, r->identity.session_id, 16);
    put(b + 48, r->id, 8); put(b + 56, r->api, 4); put(b + 60, r->schema, 4);
    put(b + 64, r->offset, 8); put(b + 72, r->bytes, 8);
    put(b + 80, r->status, 4); put(b + 84, r->domain, 4); put(b + 88, r->result, 4);
    put(b + 92, r->kind == 2, 4);
}
static int decode(const uint8_t *b, struct record *r)
{
    if (memcmp(b, "FLYTSHM1", 8) || get(b + 8, 2) != 1 || get(b + 10, 2))
        return FLYT_SHM_BAD_VERSION;
    r->kind = (uint16_t)get(b + 12, 2);
    if ((r->kind != 1 && r->kind != 2) || get(b + 14, 2) || nonzero(b + 96, 32) ||
        get(b + 92, 4) != (uint64_t)(r->kind == 2)) return FLYT_SHM_BAD_DESCRIPTOR;
    memcpy(r->identity.channel_generation, b + 16, 16);
    memcpy(r->identity.session_id, b + 32, 16);
    r->id = get(b + 48, 8); r->api = (uint32_t)get(b + 56, 4);
    r->schema = (uint32_t)get(b + 60, 4); r->offset = get(b + 64, 8);
    r->bytes = get(b + 72, 8); r->status = (uint32_t)get(b + 80, 4);
    r->domain = (uint32_t)get(b + 84, 4); r->result = (uint32_t)get(b + 88, 4);
    if (!r->id || r->id == UINT64_MAX || !r->api || !r->schema ||
        r->status > FLYT_SHM_INTERNAL_ERROR || r->status == FLYT_SHM_QUEUE_FULL ||
        r->domain > FLYT_RESULT_LIBRARY ||
        (r->kind == 1 && (r->status || r->domain || r->result)) ||
        (r->status && (r->domain || r->result || r->bytes)) ||
        (!r->domain && r->result)) return FLYT_SHM_BAD_DESCRIPTOR;
    return 0;
}
/* Producer's own tail and consumer's own head must match private shadows.
 * Peer movement is monotonic and cannot pass local counters. One in-flight
 * contract further limits occupancy to one regardless of physical capacity.
 */
static int space(struct ring *r)
{
    uint64_t head = acquire(r->base + 64);
    if (acquire(r->base) != r->local || head < r->peer || head > r->local ||
        r->local - head > 1 || r->local == UINT64_MAX) return FLYT_SHM_BAD_DESCRIPTOR;
    r->peer = head;
    return head == r->local ? 0 : FLYT_SHM_QUEUE_FULL;
}
static int peek(struct ring *r, struct record *record)
{
    uint8_t b[128];
    uint64_t tail = acquire(r->base);
    if (acquire(r->base + 64) != r->local || tail < r->peer || tail < r->local ||
        tail - r->local > 1 || r->local == UINT64_MAX) return FLYT_SHM_BAD_DESCRIPTOR;
    r->peer = tail;
    if (tail == r->local) return FLYT_SHM_AGAIN;
    snapshot(b, r->base + 128 + (r->local & (r->entries - 1)) * 128, 128);
    return decode(b, record);
}
static void push(struct ring *r, const struct record *record)
{
    uint8_t b[128];
    encode(b, record);
    memcpy(r->base + 128 + (r->local & (r->entries - 1)) * 128, b, 128);
    publish(r->base, ++r->local);
}
static void consume(struct ring *r) { publish(r->base + 64, ++r->local); }
static int matches(struct flyt_shm_channel *c, const struct record *r, uint16_t kind)
{
    if (memcmp(r->identity.channel_generation, c->identity.channel_generation, 16) ||
        memcmp(r->identity.session_id, c->identity.session_id, 16))
        return FLYT_SHM_STALE_IDENTITY;
    return r->kind == kind ? 0 : FLYT_SHM_BAD_DESCRIPTOR;
}

int flyt_shm_submit(struct flyt_shm_channel *c, const struct flyt_shm_request *q)
{
    struct record r = {0};
    uint64_t now, delta;
    int rc = usable(c, FLYT_SHM_GUEST);
    if (rc) return rc;
    if (c->pending) return FLYT_SHM_QUEUE_FULL;
    if (!q || q->request_id != c->next || c->next == UINT64_MAX || !q->api_id ||
        !q->payload_schema || q->input_bytes > c->request_bytes ||
        q->input_bytes > FLYT_SHM_MAX_MESSAGE_BYTES ||
        (q->input_bytes && !q->input)) return FLYT_SHM_BAD_DESCRIPTOR;
    if (memcmp(q->identity.channel_generation, c->identity.channel_generation, 16) ||
        memcmp(q->identity.session_id, c->identity.session_id, 16)) return FLYT_SHM_STALE_IDENTITY;
    rc = space(&c->req);
    if (rc) return rc == FLYT_SHM_QUEUE_FULL ? rc : fail(c, rc);
    rc = ticks(&now); delta = (uint64_t)c->wait_ms * 1000000;
    if (rc || now > UINT64_MAX - delta) return fail(c, FLYT_SHM_INTERNAL_ERROR);
    r.kind = 1; r.identity = c->identity; r.id = q->request_id;
    r.api = q->api_id; r.schema = q->payload_schema; r.bytes = q->input_bytes;
    if (q->input_bytes) memcpy(c->request_payload, q->input, q->input_bytes);
    c->active = r.id; c->api = r.api; c->schema = r.schema;
    c->pending = 1; c->deadline_ns = now + delta;
    push(&c->req, &r);
    return 0;
}
int flyt_shm_worker_take(struct flyt_shm_channel *c, struct flyt_shm_request *q)
{
    struct record r;
    uint8_t *payload = NULL;
    int rc = usable(c, FLYT_SHM_WORKER);
    if (rc) return rc;
    if (!q) return FLYT_SHM_BAD_DESCRIPTOR;
    memset(q, 0, sizeof(*q));
    if (c->pending) return FLYT_SHM_AGAIN;
    /* Previous response must be consumed before reusing its arena/executing. */
    rc = space(&c->resp);
    if (rc == FLYT_SHM_QUEUE_FULL) return FLYT_SHM_AGAIN;
    if (rc) return fail(c, rc);
    rc = peek(&c->req, &r);
    if (rc) return rc == FLYT_SHM_AGAIN ? rc : fail(c, rc);
    rc = matches(c, &r, 1);
    if (rc) return fail(c, rc);
    if (r.id != c->next || r.bytes > FLYT_SHM_MAX_MESSAGE_BYTES || r.offset > c->request_bytes ||
        r.bytes > c->request_bytes - (size_t)r.offset) return fail(c, FLYT_SHM_BAD_DESCRIPTOR);
    if (r.bytes) {
        payload = malloc((size_t)r.bytes);
        if (!payload) return FLYT_SHM_INTERNAL_ERROR; /* not consumed or executed */
        snapshot(payload, c->request_payload + r.offset, (size_t)r.bytes);
    }
    q->identity = r.identity; q->request_id = r.id; q->api_id = r.api;
    q->payload_schema = r.schema; q->input = payload; q->input_bytes = (size_t)r.bytes;
    c->active = r.id; c->api = r.api; c->schema = r.schema; c->pending = 1;
    consume(&c->req);
    return 0;
}
void flyt_shm_request_release(struct flyt_shm_request *q)
{
    if (q) { free((void *)q->input); memset(q, 0, sizeof(*q)); }
}
size_t flyt_shm_response_capacity(const struct flyt_shm_channel *c)
{
    if (!c) return 0;
    return c->response_bytes < FLYT_SHM_MAX_MESSAGE_BYTES ?
        c->response_bytes : FLYT_SHM_MAX_MESSAGE_BYTES;
}
int flyt_shm_worker_respond(struct flyt_shm_channel *c, const struct flyt_shm_response *s)
{
    struct record r = {0};
    uint8_t encoded[128];
    int rc = usable(c, FLYT_SHM_WORKER);
    if (rc) return rc;
    if (!c->pending || !s || s->output_bytes > flyt_shm_response_capacity(c) ||
        s->output_bytes > s->output_capacity || (s->output_bytes && !s->output))
        return FLYT_SHM_BAD_DESCRIPTOR;
    r.kind = 2; r.identity = c->identity; r.id = c->active;
    r.api = c->api; r.schema = c->schema; r.bytes = s->output_bytes;
    r.status = s->transport_status; r.domain = s->result_domain; r.result = s->api_result;
    encode(encoded, &r);
    rc = decode(encoded, &r); /* same semantic checks for local responses */
    if (rc) return rc;
    rc = space(&c->resp);
    if (rc) return rc == FLYT_SHM_QUEUE_FULL ? rc : fail(c, rc);
    if (s->output_bytes) memcpy(c->response_payload, s->output, s->output_bytes);
    push(&c->resp, &r);
    c->pending = 0; ++c->next;
    return 0;
}
int flyt_shm_try_receive(struct flyt_shm_channel *c, uint64_t id, struct flyt_shm_response *s)
{
    struct record r;
    uint64_t now;
    int rc = usable(c, FLYT_SHM_GUEST);
    if (rc) return rc;
    if (!s || !c->pending || id != c->active) return FLYT_SHM_BAD_DESCRIPTOR;
    s->output_bytes = 0; s->transport_status = 0; s->result_domain = 0; s->api_result = 0;
    if (ticks(&now) || now >= c->deadline_ns) return fail(c, FLYT_SHM_EXECUTION_UNKNOWN);
    rc = peek(&c->resp, &r);
    if (rc) return rc == FLYT_SHM_AGAIN ? rc : fail(c, rc);
    rc = matches(c, &r, 2);
    if (rc) return fail(c, rc);
    if (r.id != c->active || r.api != c->api || r.schema != c->schema ||
        r.bytes > FLYT_SHM_MAX_MESSAGE_BYTES || r.offset > c->response_bytes ||
        r.bytes > c->response_bytes - (size_t)r.offset)
        return fail(c, FLYT_SHM_BAD_DESCRIPTOR);
    s->output_bytes = (size_t)r.bytes;
    if (r.bytes > s->output_capacity || (r.bytes && !s->output))
        return FLYT_SHM_BAD_DESCRIPTOR; /* retry receive, not execution */
    if (r.bytes) snapshot(s->output, c->response_payload + r.offset, (size_t)r.bytes);
    s->transport_status = r.status; s->result_domain = r.domain; s->api_result = r.result;
    consume(&c->resp);
    c->pending = 0; ++c->next;
    return 0;
}
int flyt_shm_receive(struct flyt_shm_channel *c, uint64_t id, struct flyt_shm_response *s)
{
    int rc;
    for (;;) {
        struct timespec pause = {0, 1000000};
        rc = flyt_shm_try_receive(c, id, s);
        if (rc != FLYT_SHM_AGAIN) return rc;
        if (nanosleep(&pause, NULL) && errno != EINTR)
            return fail(c, FLYT_SHM_EXECUTION_UNKNOWN);
    }
}
void flyt_shm_abort(struct flyt_shm_channel *c)
{ if (c && pthread_equal(c->owner, pthread_self())) c->failed = 1; }
void flyt_shm_close(struct flyt_shm_channel *c)
{ if (c && pthread_equal(c->owner, pthread_self())) free(c); }
