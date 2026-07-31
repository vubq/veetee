#!/usr/bin/env sh
# ES modules cần HTTP; mở bằng file:// sẽ bị chặn.
cd "$(dirname "$0")" || exit 1
exec python3 -m http.server "${1:-8080}"
