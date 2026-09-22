#define _POSIX_C_SOURCE 200809L
#include "flyt_mapping.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

static uint64_t le(const unsigned char *p, int n) {
    uint64_t v=0; for(int i=0;i<n;i++)v|=(uint64_t)p[i]<<(8*i); return v;
}
int flyt_layout_read(const char *path, struct flyt_shm_layout *l) {
    unsigned char b[64+80*32]; struct stat st; size_t got=0; ssize_t n;
    int fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW); if(fd<0)return -1;
    /* Kubelet fsGroup makes a local PVC's 0640 metadata files 0660. Trust
     * group writes only for the same UID/GID that owns this process and the
     * allocation. Other-writable files and foreign group writers stay invalid. */
    if(fstat(fd,&st)||!S_ISREG(st.st_mode)||(st.st_mode&0002)||
       ((st.st_mode&0020)&&(st.st_uid!=geteuid()||st.st_gid!=getegid()))||
       st.st_size<64||st.st_size>(off_t)sizeof(b)){close(fd);return -1;}
    while(got<(size_t)st.st_size){n=read(fd,b+got,(size_t)st.st_size-got);if(n<=0){close(fd);return -1;}got+=(size_t)n;}
    close(fd); memset(l,0,sizeof(*l));
    memcpy(l->allocation_id,b,16);memcpy(l->channel_generation,b+16,16);
    l->region_bytes=le(b+32,8);l->session_count=(uint32_t)le(b+40,4);
    if(l->session_count>32||got!=64+80*l->session_count)return -1;
    for(int i=44;i<64;i++)if(b[i])return -1;
    for(unsigned i=0;i<l->session_count;i++){
        const unsigned char *p=b+64+80*i;struct flyt_shm_slot *s=&l->slots[i];
        memcpy(s->session_id,p,16);
        s->request_ring.offset=le(p+16,8);s->request_ring.entries=(uint32_t)le(p+24,4);
        s->response_ring.offset=le(p+32,8);s->response_ring.entries=(uint32_t)le(p+40,4);
        if(le(p+28,4)||le(p+44,4))return -1;
        s->request_payload.offset=le(p+48,8);s->request_payload.bytes=le(p+56,8);
        s->response_payload.offset=le(p+64,8);s->response_payload.bytes=le(p+72,8);
    }
    return flyt_shm_layout_validate(l)?-1:0;
}
static int map_fd(int fd,size_t bytes,struct flyt_mapping *m){
    void *p=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_SHARED,fd,0);
    if(p==MAP_FAILED){close(fd);return -1;}m->address=p;m->bytes=bytes;m->fd=fd;return 0;
}
int flyt_map_file(const char *path,const struct flyt_shm_layout *l,struct flyt_mapping *m){
    struct stat st;int fd;
    if(flyt_shm_layout_validate(l))return -1;
    fd=open(path,O_RDWR|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return -1;
    if(fstat(fd,&st)||!S_ISREG(st.st_mode)||(uint64_t)st.st_size!=l->region_bytes||st.st_nlink!=1){close(fd);return -1;}
    return map_fd(fd,(size_t)l->region_bytes,m);
}
static int attr(const char *bdf,const char *key,unsigned expected){
    char path[128];unsigned v=0;snprintf(path,sizeof(path),"/sys/bus/pci/devices/%s/%s",bdf,key);
    FILE *f=fopen(path,"r");if(!f)return -1;int rc=fscanf(f,"%x",&v);fclose(f);return rc==1&&v==expected?0:-1;
}
int flyt_map_guest(const char *bdf,const struct flyt_shm_layout *l,struct flyt_mapping *m){
    char path[128];struct stat st;int fd;
    if(!bdf||strlen(bdf)!=12||flyt_shm_layout_validate(l))return -1;
    for(int i=0;i<12;i++)if(i==4||i==7){if(bdf[i]!=':')return -1;}else if(i==10){if(bdf[i]!='.')return -1;}else if(!strchr("0123456789abcdefABCDEF",bdf[i]))return -1;
    if(attr(bdf,"vendor",0x1af4)||attr(bdf,"device",0x1110)||attr(bdf,"revision",1))return -1;
    snprintf(path,sizeof(path),"/sys/bus/pci/devices/%s/resource2",bdf);
    fd=open(path,O_RDWR|O_CLOEXEC|O_NOFOLLOW);if(fd<0)return -1;
    if(fstat(fd,&st)||(uint64_t)st.st_size!=l->region_bytes){close(fd);return -1;}
    return map_fd(fd,(size_t)l->region_bytes,m);
}
void flyt_unmap(struct flyt_mapping *m){if(m&&m->address){munmap(m->address,m->bytes);close(m->fd);memset(m,0,sizeof(*m));m->fd=-1;}}
