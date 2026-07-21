#!/usr/bin/env bash
set -euo pipefail

server_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="$server_root/tmp/runtime"
extract_root="$runtime_root/root"
runtime_lib="$extract_root/usr/lib/x86_64-linux-gnu"
pg_bin="$extract_root/usr/lib/postgresql/16/bin"
redis_bin="$extract_root/usr/bin"

if [[ ! -x "$pg_bin/postgres" || ! -x "$redis_bin/redis-server" ]]; then
  echo "Run npm run infra:host:prepare first." >&2
  exit 1
fi

export LD_LIBRARY_PATH="$runtime_lib"
if ! "$pg_bin/pg_isready" -h 127.0.0.1 -p 5432 -U veetee >/dev/null 2>&1; then
  "$pg_bin/pg_ctl" \
    -D "$runtime_root/postgres-data" \
    -l "$runtime_root/postgres.log" \
    -o "-p 5432 -k $runtime_root/postgres-socket -h 127.0.0.1" \
    start
fi

if ! "$pg_bin/psql" -h 127.0.0.1 -p 5432 -U veetee -d postgres \
  -tAc "SELECT 1 FROM pg_database WHERE datname = 'veetee'" | rg -q '^1$'; then
  "$pg_bin/createdb" -h 127.0.0.1 -p 5432 -U veetee veetee
fi

if ! "$redis_bin/redis-cli" -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
  "$redis_bin/redis-server" \
    --port 6379 \
    --bind 127.0.0.1 \
    --protected-mode yes \
    --dir "$runtime_root/redis-data" \
    --appendonly yes \
    --daemonize yes \
    --pidfile "$runtime_root/redis.pid" \
    --logfile "$runtime_root/redis.log"
fi

"$pg_bin/pg_isready" -h 127.0.0.1 -p 5432 -U veetee
"$redis_bin/redis-cli" -h 127.0.0.1 -p 6379 ping
