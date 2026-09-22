/* Regression for the real backward failure: an adjacent allocation's base
 * must not resolve to the preceding allocation's one-past address. No GPU. */
#include "../../runtime/shm/src/guest.c"
#include <assert.h>
int main(void){
    void *area=mmap(NULL,8192,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
    assert(area!=MAP_FAILED);
    allocations[0].base=area;allocations[0].bytes=4096;allocations[0].handle=11;
    allocations[1].base=(char*)area+4096;allocations[1].bytes=4096;allocations[1].handle=22;
    struct flyt_device_ref ref;
    assert(!flyt_guest_ref((char*)area+4096,0,&ref)&&ref.handle==22&&ref.offset==0);
    assert(!flyt_guest_ref((char*)area+4096,4096,&ref)&&ref.handle==22);
    assert(!flyt_guest_ref((char*)area+4095,1,&ref)&&ref.handle==11&&ref.offset==4095);
    assert(flyt_guest_ref((char*)area+4095,2,&ref));
    assert(!flyt_guest_ref((char*)area+8192,0,&ref)&&ref.handle==22&&ref.offset==4096);
    assert(flyt_guest_ref((char*)area+8192,1,&ref));
    /* Collision must preserve the existing mapping and its contents. */
    *(volatile unsigned char*)area=0x5a;
    assert(reserve_device_va((uintptr_t)area,4096)==MAP_FAILED);
    assert(*(volatile unsigned char*)area==0x5a);
    assert(reserve_device_va((uintptr_t)area+1,4096)==MAP_FAILED);
    assert(reserve_device_va(UINT64_MAX-4095,8192)==MAP_FAILED);
    munmap(area,8192);memset(allocations,0,sizeof(allocations));
    void *reserved=reserve_device_va((uintptr_t)area,8192);
    assert(reserved==area);munmap(reserved,8192);
    puts("PASS: adjacent refs, bounds, VA collision preservation and exact reservation");
    return 0;
}
