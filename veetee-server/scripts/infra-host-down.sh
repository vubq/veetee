#!/usr/bin/env bash
set -euo pipefail

server_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="$server_root/tmp/runtime"
extract_root="$runtime_root/root"
runtime_lib="$extract_root/usr/lib/x86_64-linux-gnu"
pg_bin="$extract_root/usr/lib/postgresql/16/bin"
redis_bin="$extract_root/usr/bin"

export LD_LIBRARY_PATH="$runtime_lib"
if [[ -x "$redis_bin/redis-cli" ]] && "$redis_bin/redis-cli" -h 127.0.0.1 -p 6379 ping \
  >/dev/null 2>&1; then
  "$redis_bin/redis-cli" -h 127.0.0.1 -p 6379 shutdown save
fi

if [[ -x "$pg_bin/pg_ctl" ]] && "$pg_bin/pg_ctl" -D "$runtime_root/postgres-data" status \
  >/dev/null 2>&1; then
  "$pg_bin/pg_ctl" -D "$runtime_root/postgres-data" stop -m fast
fi
