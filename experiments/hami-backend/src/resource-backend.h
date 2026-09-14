#ifndef FLYT_RESOURCE_BACKEND_H
#define FLYT_RESOURCE_BACKEND_H
#include <stdint.h>
#include "cpu-server-resource-controller.h"

typedef struct {
    int (*change_sm)(uint32_t);
    void (*set_device)(int);
    int (*get_device)(void);
    int (*set_limit)(uint64_t);
    uint64_t (*get_limit)(void);
    int (*allow_alloc)(uint64_t);
    uint64_t (*get_free)(void);
    void (*inc_usage)(uint64_t);
    void (*dec_usage)(uint64_t);
    void (*apply_change)(void);
    int (*set_config)(uint32_t, uint64_t);
    int (*init)(uint32_t, uint64_t);
    void (*push_primary)(void);
    void (*pop_primary)(void);
    void (*set_exec)(void);
} FlytResourceBackend;
extern const FlytResourceBackend flyt_hami_ops;
#ifndef FLYT_HAMI_ONLY
extern const FlytResourceBackend flyt_mps_ops;
#endif
extern volatile uint32_t cur_num_sm_cores;
int flyt_hami_memory(uint64_t *available, uint64_t *total);
#endif
