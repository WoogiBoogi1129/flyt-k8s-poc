#!/usr/bin/env bash
set -Eeuo pipefail

exec gdb -q -batch \
  -ex 'set pagination off' \
  -ex 'handle SIGPIPE nostop noprint pass' \
  -ex 'break rpc_cd_prog_1 if rqstp->rq_proc == 1026' \
  -ex 'break rpc_cumoduleloaddata_1_svc' \
  -ex run \
  -ex 'print rqstp->rq_proc' \
  -ex 'thread apply all bt' \
  -ex continue \
  -ex 'print mem.mem_data_len' \
  -ex 'thread apply all bt' \
  -ex continue \
  -ex 'thread apply all bt full' \
  --args /opt/flyt/bin/cricket-rpc-server.real "$@"
