#include <cuda.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    CUresult result;
    CUdevice device = 0;
    CUcontext context = NULL;
    CUmodule module = NULL;
    unsigned char *image = NULL;
    long image_size = 0;
    FILE *file = NULL;
    const char *name = NULL;
    const char *message = NULL;

    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: %s MODULE [data]\n", argv[0]);
        return 2;
    }
    result = cuInit(0);
    if (result == CUDA_SUCCESS) {
        result = cuDeviceGet(&device, 0);
    }
    if (result == CUDA_SUCCESS) {
        result = cuCtxCreate(&context, 0, device);
    }
    if (result == CUDA_SUCCESS) {
        if (argc == 2) {
            result = cuModuleLoad(&module, argv[1]);
        } else {
            file = fopen(argv[1], "rb");
            if (file == NULL || fseek(file, 0, SEEK_END) != 0 ||
                (image_size = ftell(file)) <= 0 ||
                fseek(file, 0, SEEK_SET) != 0) {
                result = CUDA_ERROR_FILE_NOT_FOUND;
            } else {
                image = malloc((size_t)image_size);
                if (image == NULL ||
                    fread(image, 1, (size_t)image_size, file) !=
                        (size_t)image_size) {
                    result = CUDA_ERROR_OUT_OF_MEMORY;
                } else {
                    result = cuModuleLoadData(&module, image);
                }
            }
            if (file != NULL) fclose(file);
        }
    }
    cuGetErrorName(result, &name);
    cuGetErrorString(result, &message);
    printf("result=%d name=%s message=%s module=%p\n", result,
           name ? name : "unknown", message ? message : "unknown",
           (void *)module);
    if (module != NULL) {
        cuModuleUnload(module);
    }
    if (context != NULL) {
        cuCtxDestroy(context);
    }
    free(image);
    return result == CUDA_SUCCESS ? 0 : 1;
}
