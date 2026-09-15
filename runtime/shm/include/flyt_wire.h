#ifndef FLYT_WIRE_H
#define FLYT_WIRE_H
#include "flyt_cuda_exec.h"
#include "flyt_mapping.h"
#define FLYT_HELLO 1u
#define FLYT_GOODBYE 2u
#define FLYT_HEARTBEAT 3u
static inline uint64_t flyt_get(const uint8_t *p,unsigned n){uint64_t x=0;for(unsigned i=0;i<n;i++)x|=(uint64_t)p[i]<<(8*i);return x;}
static inline void flyt_put(uint8_t *p,uint64_t x,unsigned n){for(unsigned i=0;i<n;i++)p[i]=(uint8_t)(x>>(8*i));}
struct flyt_cuda_session { struct flyt_cuda_exec *exec; };
#endif
