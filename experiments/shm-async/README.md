# 10-07 SHM async — source authored, NOT_RUN

stream/event handle, query/synchronize, HtoD pinned staging + completion event, DtoD async,
PTX module/function 및 명시적 kernel ABI에 따른 Driver launch 경로를 추가했다.
Worker idle loop에서 완료 event를 확인한 뒤 staging을 회수한다. 중간 오류 시 in-flight buffer를
성급히 free하지 않고 해당 프로세스를 종료한다. 정상 종료는 synchronize 뒤 정리한다.

초기 DtoH async API는 stream 완료까지 기다린 뒤 결과를 전달하는 보수적 경로다.
호출자에게 미완료 데이터를 반환하지 않지만 전송 겹침/성능 동등성을 보장하지 않는다.
staging은 최대 128개이며 각 전송 최대 16MiB다. queue API_RETURN을 GPU 완료로 일반화하지 않는다.

Kernel launch는 PTX와 cuModuleGetFunction, 그리고 별도 `flytRegisterKernelABI`를 통해 지정한
각 인자의 크기/포인터 여부를 요구한다. Driver cuLaunchKernel의 kernelParams 경로만 지원하고
extra packed buffer, cubin/fatbin, Runtime fatbinary 등록, 자동 PyTorch kernel ABI 추론은 지원하지 않는다.
미지원 입력은 명시적으로 거절한다. 모든 CUDA kernel 호환성을 완성했다는 의미가 아니다.

API ID 0x2001..은 stream, 0x2010..은 event, 0x2020은 async copy,
0x2030..은 module/function/kernel이다. schema=1, scalar LE, pointer는 handle+offset이다.
kernel header 48 bytes 뒤 인자별 type/size u32 + value/ref 16 bytes record를 사용한다.
Graph/library와 실제 호환성 지원 범위는 10-08에서 관리한다. 모든 검증 NOT_RUN.
