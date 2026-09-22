/* Direct CUDA allocation probe. Run in one VM (both child processes) for E2.
 * Standalone HAMi container runs are only prerequisite diagnostics.
 * No kernel is required; each forked child execs before CUDA initialization.
 */
#include <cuda_runtime_api.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static int transfer(int fd, void *data, size_t count, int output) {
    char *p = (char *)data;
    while (count) {
        ssize_t n = output ? write(fd, p, count) : read(fd, p, count);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        p += n; count -= (size_t)n;
    }
    return 0;
}

static int initialized(void) {
    int count = 0;
    cudaError_t e = cudaGetDeviceCount(&count);
    if (e != cudaSuccess || count != 1) return 1;
    return cudaFree(0) == cudaSuccess ? 0 : 1;
}

static int race_child(char **argv) {
    alarm(60);
    size_t request=(size_t)strtoull(argv[2],NULL,10);
    int ready_fd=atoi(argv[3]),go_fd=atoi(argv[4]),result_fd=atoi(argv[5]),release_fd=atoi(argv[6]);
    int init=initialized(),token=0;
    if(transfer(ready_fd,&init,sizeof(init),1)||transfer(go_fd,&token,sizeof(token),0))return 12;
    void *memory=NULL;
    int status=init?-1:(int)cudaMalloc(&memory,request);
    if(transfer(result_fd,&status,sizeof(status),1)||transfer(release_fd,&token,sizeof(token),0))return 13;
    return memory&&cudaFree(memory)!=cudaSuccess?14:0;
}

static int race(size_t quota) {
    int ready[2][2], go[2][2], result[2][2], release[2][2];
    pid_t pids[2] = {-1, -1};
    for (int i = 0; i < 2; ++i)
        if (pipe(ready[i]) || pipe(go[i]) || pipe(result[i]) || pipe(release[i])) return 10;
    const size_t request = quota / 5 * 3;
    for (int i = 0; i < 2; ++i) {
        pids[i] = fork();
        if (pids[i] < 0) return 11;
        if (pids[i] == 0) {
            if (getenv("FLYT_LAYOUT")) setenv("FLYT_SLOT", i ? "1" : "0", 1);
            for(int j=0;j<2;j++){
                close(ready[j][0]);close(go[j][1]);close(result[j][0]);close(release[j][1]);
                if(j!=i){close(ready[j][1]);close(go[j][0]);close(result[j][1]);close(release[j][0]);}
            }
            char bytes[32],r[16],g[16],s[16],e[16];
            snprintf(bytes,sizeof(bytes),"%zu",request);snprintf(r,sizeof(r),"%d",ready[i][1]);
            snprintf(g,sizeof(g),"%d",go[i][0]);snprintf(s,sizeof(s),"%d",result[i][1]);snprintf(e,sizeof(e),"%d",release[i][0]);
            // The SHM guest intentionally rejects forked library state. exec gives
            // each preallocated slot a fresh library and independent I/O thread.
            execl("/proc/self/exe","memory-probe","race-child",bytes,r,g,s,e,(char *)NULL);
            _exit(127);
        }
    }
    for(int i=0;i<2;i++){close(ready[i][1]);close(go[i][0]);close(result[i][1]);close(release[i][0]);}
    int init[2] = {-1, -1}, status[2] = {-1, -1}, token = 1;
    for (int i = 0; i < 2; ++i) if (transfer(ready[i][0], &init[i], sizeof(int), 0)) return 15;
    for (int i = 0; i < 2; ++i) if (transfer(go[i][1], &token, sizeof(token), 1)) return 16;
    for (int i = 0; i < 2; ++i) if (transfer(result[i][0], &status[i], sizeof(int), 0)) return 17;
    for (int i = 0; i < 2; ++i) if (transfer(release[i][1], &token, sizeof(token), 1)) return 18;
    int children_ok = 1;
    for (int i = 0; i < 2; ++i) {
        int s;
        if (waitpid(pids[i], &s, 0) < 0 || !WIFEXITED(s) || WEXITSTATUS(s) != 0) children_ok = 0;
    }
    const int pass = children_ok && !init[0] && !init[1] &&
        ((status[0] == cudaSuccess && status[1] == cudaErrorMemoryAllocation) ||
         (status[1] == cudaSuccess && status[0] == cudaErrorMemoryAllocation));
    const int inconclusive = !init[0] && !init[1] && status[0] == cudaErrorMemoryAllocation && status[1] == cudaErrorMemoryAllocation;
    printf("{\"scenario\":\"aggregate_race\",\"quota_bytes\":%zu,\"request_per_process\":%zu,\"cuda_status\":[%d,%d],\"status\":\"%s\"}\n",
           quota, request, status[0], status[1], pass ? "PASS" : inconclusive ? "BLOCKED" : "FAIL");
    return pass ? 0 : inconclusive ? 3 : 1;
}

static int single(const char *scenario, size_t bytes) {
    if (initialized()) return 20;
    size_t free_bytes = 0, total_bytes = 0;
    cudaError_t info = cudaMemGetInfo(&free_bytes, &total_bytes);
    void *memory = NULL;
    cudaError_t first = cudaMalloc(&memory, bytes), released = cudaSuccess, second = cudaSuccess;
    if (first == cudaSuccess) released = cudaFree(memory);
    const int over = !strcmp(scenario, "over");
    if (!strcmp(scenario, "free_reallocate") && first == cudaSuccess && released == cudaSuccess) {
        memory = NULL; second = cudaMalloc(&memory, bytes);
        if (second == cudaSuccess) released = cudaFree(memory);
    }
    const int pass = over ? first == cudaErrorMemoryAllocation : first == cudaSuccess && released == cudaSuccess && second == cudaSuccess;
    printf("{\"scenario\":\"%s\",\"requested_bytes\":%zu,\"info_status\":%d,\"free_bytes\":%zu,\"total_bytes\":%zu,\"cuda_status\":%d,\"release_status\":%d,\"reallocate_status\":%d,\"status\":\"%s\"}\n",
           scenario, bytes, (int)info, free_bytes, total_bytes, (int)first, (int)released, (int)second, pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}

static int suite(size_t quota) {
    if(initialized())return 20;
    size_t before=0,total=0,after=0,total_after=0;
    cudaError_t info=cudaMemGetInfo(&before,&total);
    if(info!=cudaSuccess||total!=quota||!before||before>quota){
        printf("{\"scenario\":\"query_quota\",\"status\":\"FAIL\",\"cuda_status\":%d,\"free_bytes\":%zu,\"total_bytes\":%zu,\"quota_bytes\":%zu}\n",(int)info,before,total,quota);
        return 1;
    }
    /* Dynamic remainder comes from the installed limiter, not physical VRAM.
     * This diagnostic records the exact remainder used for boundary requests.
     */
    int failed=0;
    failed|=single("below",before/2);
    failed|=single("boundary",before);
    failed|=single("over",before+1);
    failed|=single("free_reallocate",before/2);
    info=cudaMemGetInfo(&after,&total_after);
    int restored=info==cudaSuccess&&after==before&&total_after==total;
    printf("{\"scenario\":\"accounting_restored\",\"status\":\"%s\",\"before_free\":%zu,\"after_free\":%zu}\n",restored?"PASS":"FAIL",before,after);
    return failed||!restored;
}

static int run_probe(int argc, char **argv) {
    if(argc==7&&!strcmp(argv[1],"race-child"))return race_child(argv);
    if (argc != 3) {
        fprintf(stderr, "usage: memory-probe below|boundary|over|free_reallocate BYTES | aggregate_race QUOTA_BYTES\n");
        return 2;
    }
    char *end = NULL;
    errno = 0;
    unsigned long long amount = strtoull(argv[2], &end, 10);
    if (errno || !end || *end || !amount || argv[2][0] == '-' || amount > SIZE_MAX) return 2;
    alarm(70);
    if (!strcmp(argv[1], "suite")) return suite((size_t)amount);
    if (!strcmp(argv[1], "aggregate_race")) return race((size_t)amount);
    if (strcmp(argv[1], "below") && strcmp(argv[1], "boundary") && strcmp(argv[1], "over") && strcmp(argv[1], "free_reallocate")) return 2;
    return single(argv[1], (size_t)amount);
}
int main(int argc,char **argv){
    int result=run_probe(argc,argv);
    /* Keep successful child sessions observable before GOODBYE. */
    const char *hold=getenv("FLYT_PROBE_HOLD");
    /* The aggregate parent owns no GPU session. After both children exit the
     * controller may stop the VM, so do not delay its final result/exit again. */
    const int aggregate_parent=argc==3&&!strcmp(argv[1],"aggregate_race");
    if(hold&&!aggregate_parent){unsigned seconds=(unsigned)strtoul(hold,NULL,10);fflush(stdout);if(seconds<=30)sleep(seconds);}
    return result;
}
