#!/usr/bin/env bash
set -euo pipefail
native_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${GCC_BIN:-}" ]]; then
  export PATH="$GCC_BIN:$PATH"
elif [[ -f /c/mingw64/bin/gcc.exe ]]; then
  export PATH="/c/mingw64/bin:$PATH"
fi
gcc -std=c11 -O3 -Wall -Wextra -Wpedantic -Werror -static-libgcc -municode \
  "$native_dir/controller.c" "$native_dir/json.c" "$native_dir/platform.c" \
  -o "$native_dir/run_2026_all.exe" -lbcrypt
printf 'Built %s/run_2026_all.exe\n' "$native_dir"
