#ifndef FLYT_MAPPING_H
#define FLYT_MAPPING_H
#include "flyt_shm_queue.h"
struct flyt_mapping { void *address; size_t bytes; int fd; };
/* layout.bin is a private provisioning artifact, never a native struct dump. */
int flyt_layout_read(const char *path, struct flyt_shm_layout *out);
int flyt_map_file(const char *path, const struct flyt_shm_layout *, struct flyt_mapping *);
/* Explicit BDF only: no PCI scanning, driver binding, reset or enable writes. */
int flyt_map_guest(const char *bdf, const struct flyt_shm_layout *, struct flyt_mapping *);
void flyt_unmap(struct flyt_mapping *);
#endif
