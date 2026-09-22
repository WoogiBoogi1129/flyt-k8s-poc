#define _GNU_SOURCE
#include "flyt_mapping.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
#define OK(expr) do { int check_result=(expr); if(check_result) {fprintf(stderr,"%s => %d\n",#expr,check_result);exit(1);} } while(0)
int main(int argc,char **argv) {
  if(argc!=3)return 2;
  alarm(30);
  struct flyt_shm_layout l;struct flyt_mapping m={0};
  OK(flyt_layout_read(argv[1],&l));OK(flyt_map_file(argv[2],&l,&m));
  int syncfd[2];if(pipe(syncfd))return 3;
  pid_t pid=fork();if(pid<0)return 4;
  struct flyt_shm_channel *c=NULL;
  if(pid==0){
    close(syncfd[0]);
    OK(flyt_shm_open(m.address,m.bytes,&l,0,FLYT_SHM_WORKER,5000,&c));
    if(write(syncfd[1],"R",1)!=1)return 5;
    for(int i=0;i<1000;i++){
      struct flyt_shm_request q={0};int rc;
      while((rc=flyt_shm_worker_take(c,&q))==FLYT_SHM_AGAIN)usleep(100);
      OK(rc);
      struct flyt_shm_response r={.output=(unsigned char*)q.input,.output_capacity=q.input_bytes,.output_bytes=q.input_bytes};
      OK(flyt_shm_worker_respond(c,&r));flyt_shm_request_release(&q);
    }
    flyt_shm_close(c);flyt_unmap(&m);return 0;
  }
  close(syncfd[1]);char ready;if(read(syncfd[0],&ready,1)!=1)return 6;
  OK(flyt_shm_open(m.address,m.bytes,&l,0,FLYT_SHM_GUEST,5000,&c));
  for(int i=0;i<1000;i++){
    char input[64],output[64];snprintf(input,sizeof(input),"cross-process-%d",i);
    struct flyt_shm_request q={.request_id=i+1,.api_id=0x1001,.payload_schema=1,
      .input=(unsigned char*)input,.input_bytes=strlen(input)+1};
    memcpy(q.identity.channel_generation,l.channel_generation,16);
    memcpy(q.identity.session_id,l.slots[0].session_id,16);
    struct flyt_shm_response r={.output=(unsigned char*)output,.output_capacity=sizeof(output)};
    OK(flyt_shm_submit(c,&q));OK(flyt_shm_receive(c,q.request_id,&r));
    if(r.transport_status||r.output_bytes!=q.input_bytes||strcmp(input,output))return 7;
  }
  int status;if(waitpid(pid,&status,0)<0||!WIFEXITED(status)||WEXITSTATUS(status))return 8;
  flyt_shm_close(c);flyt_unmap(&m);
  puts("PASS: 1000 cross-process queue round trips, including 64-entry ring wraparound");return 0;
}
