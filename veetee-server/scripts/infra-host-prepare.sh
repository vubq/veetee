#!/usr/bin/env bash
set -euo pipefail

server_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runtime_root="$server_root/tmp/runtime"
debs_root="$runtime_root/debs"
extract_root="$runtime_root/root"

mkdir -p "$debs_root" "$extract_root" "$runtime_root/postgres-data" \
  "$runtime_root/postgres-socket" "$runtime_root/redis-data"

packages=(
  postgresql-16
  postgresql-client-16
  postgresql-common
  postgresql-client-common
  libpq5
  libllvm17t64
  redis-server
  redis-tools
  libjemalloc2
  liblzf1
)

(
  cd "$debs_root"
  apt-get download "${packages[@]}"
)

for package_file in "$debs_root"/*.deb; do
  dpkg-deb -x "$package_file" "$extract_root"
done

runtime_lib="$extract_root/usr/lib/x86_64-linux-gnu"
pg_bin="$extract_root/usr/lib/postgresql/16/bin"
if [[ ! -f "$runtime_root/postgres-data/PG_VERSION" ]]; then
  LD_LIBRARY_PATH="$runtime_lib" "$pg_bin/initdb" \
    -D "$runtime_root/postgres-data" \
    --username=veetee \
    --auth=trust \
    --no-locale \
    --encoding=UTF8
fi

echo "Host-local PostgreSQL and Redis runtime is ready under $runtime_root"
