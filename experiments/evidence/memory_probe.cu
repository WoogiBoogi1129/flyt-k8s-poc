/* Direct CUDA allocation probe. Run in one VM (both child processes) for E2.
 * Standalone HAMi container runs are only prerequisite diagnostics.
 * No kernel is required; fork happens before CUDA initialization.
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
            alarm(60);
            if (getenv("FLYT_LAYOUT")) setenv("FLYT_SLOT", i ? "1" : "0", 1);
            int init = initialized(), signal_value = 0;
            if (transfer(ready[i][1], &init, sizeof(init), 1) ||
                transfer(go[i][0], &signal_value, sizeof(signal_value), 0)) _exit(12);
            void *memory = NULL;
            int status = init ? -1 : (int)cudaMalloc(&memory, request);
            if (transfer(result[i][1], &status, sizeof(status), 1) ||
                transfer(release[i][0], &signal_value, sizeof(signal_value), 0)) _exit(13);
            // Hold successful allocations until BOTH allocation calls have returned.
            if (memory && cudaFree(memory) != cudaSuccess) _exit(14);
            exit(0);
        }
    }
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

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: memory-probe below|boundary|over|free_reallocate BYTES | aggregate_race QUOTA_BYTES\n");
        return 2;
    }
    char *end = NULL;
    errno = 0;
    unsigned long long amount = strtoull(argv[2], &end, 10);
    if (errno || !end || *end || !amount || argv[2][0] == '-' || amount > SIZE_MAX) return 2;
    alarm(70);
    if (!strcmp(argv[1], "aggregate_race")) return race((size_t)amount);
    if (strcmp(argv[1], "below") && strcmp(argv[1], "boundary") && strcmp(argv[1], "over") && strcmp(argv[1], "free_reallocate")) return 2;
    return single(argv[1], (size_t)amount);
}
