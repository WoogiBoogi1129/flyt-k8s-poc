#define _POSIX_C_SOURCE 200809L
#include "flyt_wire.h"
#include "flyt_async.h"
#include "flyt_compat.h"
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
static volatile sig_atomic_t stopping;
static void stop(int x){(void)x;stopping=1;}
int main(int argc,char **argv){
    struct flyt_shm_layout layout;struct flyt_mapping mapping={0};struct flyt_shm_channel *channel=NULL;
    struct flyt_cuda_runtime *runtime=NULL;struct flyt_cuda_session session={0};uint32_t error=0;
    char layout_path[512],backing[512],ready[128];int exitcode=1,hello=0;unsigned slot;
    if(argc!=3)return 2;char *end;unsigned long parsed=strtoul(argv[2],&end,10);
    if(*end||parsed>=32)return 2;slot=(unsigned)parsed;
    if(snprintf(layout_path,sizeof(layout_path),"%s/layout.bin",argv[1])>=(int)sizeof(layout_path)||snprintf(backing,sizeof(backing),"%s/channel",argv[1])>=(int)sizeof(backing))return 2;
    if(flyt_layout_read(layout_path,&layout)||slot>=layout.session_count||flyt_map_file(backing,&layout,&mapping))return 3;
    if(flyt_shm_open(mapping.address,mapping.bytes,&layout,slot,FLYT_SHM_WORKER,30000,&channel))goto done;
    if(flyt_cuda_runtime_open(&runtime,&error)||flyt_cuda_exec_create(flyt_cuda_runtime_backend(),runtime,&session.exec))goto done;
    signal(SIGTERM,stop);signal(SIGINT,stop);
    size_t capacity=flyt_shm_response_capacity(channel);uint8_t *output=malloc(capacity);
    if(!output)goto done;
    snprintf(ready,sizeof(ready),"/tmp/flyt-slot-%u-mapped",slot);FILE *f=fopen(ready,"wx");if(!f){free(output);goto done;}fclose(f);
    struct timespec last,now;clock_gettime(CLOCK_MONOTONIC,&last);
    while(!stopping){
        clock_gettime(CLOCK_MONOTONIC,&now);
        if(now.tv_sec-last.tv_sec>(hello?60:600))break;
        if(flyt_async_reap())break;
        struct flyt_shm_request q={0};int rc=flyt_shm_worker_take(channel,&q);
        if(rc==FLYT_SHM_AGAIN){struct timespec pause={0,1000000};nanosleep(&pause,NULL);continue;}
        if(rc)break;
        clock_gettime(CLOCK_MONOTONIC,&last);
        struct flyt_shm_response r={.output=output,.output_capacity=capacity};
        if(q.api_id==FLYT_HELLO&&!hello&&q.payload_schema==1&&!q.input_bytes){hello=1;}
        else if(!hello){r.transport_status=FLYT_SHM_CHANNEL_CLOSED;stopping=1;}
        else if((q.api_id==FLYT_HEARTBEAT||q.api_id==FLYT_GOODBYE)&&q.payload_schema==1&&!q.input_bytes){
            if(q.api_id==FLYT_GOODBYE){stopping=1;exitcode=0;}
        }else if(q.api_id==FLYT_HELLO){r.transport_status=FLYT_SHM_BAD_DESCRIPTOR;stopping=1;}
        else if(q.api_id>=0x3000){if(flyt_compat_dispatch(&session,&q,&r)){flyt_shm_request_release(&q);break;}}
        else if(q.api_id>=0x2000){if(flyt_async_dispatch(&session,&q,&r)){flyt_shm_request_release(&q);break;}}
        else if(flyt_cuda_dispatch(&session,&q,&r)){flyt_shm_request_release(&q);break;}
        rc=flyt_shm_worker_respond(channel,&r);flyt_shm_request_release(&q);if(rc)break;
        if(hello){char path[128];snprintf(path,sizeof(path),"/tmp/flyt-slot-%u-guest",slot);FILE *g=fopen(path,"a");if(g)fclose(g);}
    }
    unlink(ready);char guest_marker[128];snprintf(guest_marker,sizeof(guest_marker),"/tmp/flyt-slot-%u-guest",slot);unlink(guest_marker);free(output);
done:
    if(session.exec&&flyt_compat_close())return 1;
    if(session.exec&&flyt_async_close())return 1; /* process exit releases uncertain CUDA state */
    if(session.exec&&flyt_cuda_exec_destroy(&session.exec,&error))exitcode=1;
    if(!session.exec)flyt_cuda_runtime_close(runtime);
    flyt_shm_close(channel);flyt_unmap(&mapping);return exitcode;
}
