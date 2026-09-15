#ifndef FLYT_GUEST_H
#define FLYT_GUEST_H
#include <pthread.h>
#include "flyt_wire.h"
extern pthread_mutex_t flyt_guest_lock;
/* Caller serializes these with flyt_guest_lock. No RPC fallback. */
int flyt_guest_exchange(uint32_t api,const void *input,size_t bytes,void *output,size_t capacity,size_t *received);
int flyt_guest_ref(const void *pointer,size_t bytes,struct flyt_device_ref *out);
#endif
