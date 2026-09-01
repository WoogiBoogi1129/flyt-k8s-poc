#!/usr/bin/env bash
set -Eeuo pipefail

exec strace -ff -o /tmp/flyt-server-strace \
  -e trace=accept,accept4,close,read,readv,recvfrom,sendto,shutdown,write,writev \
  /opt/flyt/bin/cricket-rpc-server.real "$@"
