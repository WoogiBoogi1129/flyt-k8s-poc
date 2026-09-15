#ifndef FLYT_ASYNC_H
#define FLYT_ASYNC_H
#include "flyt_wire.h"
enum {
 FLYT_STREAM_CREATE=0x2001,FLYT_STREAM_DESTROY,FLYT_STREAM_SYNC,FLYT_STREAM_QUERY,
 FLYT_EVENT_CREATE=0x2010,FLYT_EVENT_DESTROY,FLYT_EVENT_RECORD,FLYT_EVENT_SYNC,FLYT_EVENT_QUERY,
 FLYT_ASYNC_COPY=0x2020,FLYT_MODULE_LOAD=0x2030,FLYT_MODULE_UNLOAD,FLYT_FUNCTION_GET,FLYT_KERNEL_LAUNCH
};
int flyt_async_dispatch(struct flyt_cuda_session *,const struct flyt_shm_request *,struct flyt_shm_response *);
int flyt_async_reap(void);
int flyt_async_close(void);
#endif
