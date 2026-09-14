#pragma once
// Fixed geometry/work across quotas; host wall time includes throttling.
extern "C" __global__ void flyt_e2e_burn(float *out) {
    unsigned i = blockIdx.x * blockDim.x + threadIdx.x;
    float x = 0.1f + float(i % 31) * 0.001f;
    #pragma unroll 1
    for (int j = 0; j < 8192; ++j) x = fmaf(x, 0.999999f, 0.000001f);
    out[i] = x;
}
