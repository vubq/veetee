# Firmware measurement profiles

These overlays isolate one allocator/CPU variable at a time. They are not
automatically merged into the release defaults.

## Accepted allocator baseline

Board A/B on 2026-07-30 rejected `mem-t2-r64` (2 KiB always-internal,
64 KiB internal reserve). The device opened WebSocket, completed hello and entered
`listening`, then raised `LoadProhibited` in the `esp_transport_read` /
`ws_read_header` path. Release and production builds retain `mem-control`
(16 KiB always-internal, 32 KiB internal reserve).

Keep `mem-t2-r64` and its derived default-TLS overlay only to reproduce this failure.
Do not select or flash either profile for production or release validation unless an
explicit incident-reproduction task requires it.

Build each variant in its own directory and its own generated `sdkconfig`:

```bash
source /home/vubq/.espressif/v6.0.2/esp-idf/export.sh
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-firmware

variant_name=mem-control-stats-160
variant_build_dir="build/ab-${variant_name}"
idf.py -B "${variant_build_dir}" \
  -DIDF_TARGET=esp32s3 \
  -DSDKCONFIG="${PWD}/${variant_build_dir}/sdkconfig" \
  -DSDKCONFIG_DEFAULTS="${PWD}/sdkconfig.defaults;${PWD}/profiles/sdkconfig.mem-control;${PWD}/profiles/sdkconfig.benchmark-runtime-stats;${PWD}/profiles/sdkconfig.cpu-160" \
  build size
```

Always pass `-DIDF_TARGET=esp32s3`; do not let a stale CMake cache choose the
target. Use these overlay sets with a fresh, uniquely named build directory:

| Variant | `SDKCONFIG_DEFAULTS` after `sdkconfig.defaults` |
|---|---|
| control stats-off 160 | `profiles/sdkconfig.mem-control;profiles/sdkconfig.cpu-160` |
| rejected reproduction stats-off 160 | `profiles/sdkconfig.mem-t2-r64;profiles/sdkconfig.cpu-160` |
| control stats 160 | `profiles/sdkconfig.mem-control;profiles/sdkconfig.benchmark-runtime-stats;profiles/sdkconfig.cpu-160` |
| rejected reproduction stats 160 | `profiles/sdkconfig.mem-t2-r64;profiles/sdkconfig.benchmark-runtime-stats;profiles/sdkconfig.cpu-160` |
| rejected default-TLS reproduction | `profiles/sdkconfig.mem-t2-r64-default-tls;profiles/sdkconfig.benchmark-runtime-stats;profiles/sdkconfig.cpu-160` |
| accepted memory profile at 240 | accepted memory overlay + `profiles/sdkconfig.benchmark-runtime-stats;profiles/sdkconfig.cpu-240` |

The paths passed to CMake must be absolute as in the example. Do not combine
`mem-control` and `mem-t2-r64` in one build.

Verify effective values before flashing:

```bash
rg -n 'SPIRAM_MALLOC|MBEDTLS_.*MEM_ALLOC|VEETEE_BENCHMARK_RUNTIME_STATS|FREERTOS_GENERATE_RUN_TIME_STATS|RUN_TIME_STATS_USING|ESP_DEFAULT_CPU_FREQ' \
  "${variant_build_dir}/sdkconfig" "${variant_build_dir}/config/sdkconfig.h"
```

Flash only the application so NVS, Wi-Fi identity and `resource_0/1` remain
untouched:

```bash
idf.py -B "${variant_build_dir}" -p /dev/ttyACM0 app-flash
idf.py -B "${variant_build_dir}" -p /dev/ttyACM0 monitor
```

Never use `erase-flash` for this A/B. It would remove Wi-Fi/device identity and
make the candidates use different NVS/resource state. Wait for `idle`, then a
host speaker can trigger the built-in technical wake profile:

```bash
spd-say -w -l en -t female1 -r -15 -i 75 "Hi E S P"
```

This only automates the `Hi ESP` bring-up model. It does not validate custom
`Hey VeeTee`, microphone FAR/FRR or physical speaker quality.

Current release comparison:

1. `mem-control` + `cpu-160`, stats off.
2. Add `benchmark-runtime-stats` for per-task/per-core evidence.
3. Only after the 160 MHz release gate passes, compare `cpu-240`.
4. Rebuild stats-off with `mem-control` for the final heap gate.

Run `mem-t2-r64` variants only for explicit crash reproduction, never as a release
candidate or a winning profile.

The ESP-IDF internal reserve can consist of fragmented regions. It does not
replace the explicit WebSocket contiguous reserve/preflight in firmware.
The transport prefers a 16 KiB contiguous reserve and may fall back to its
10 KiB I/O-task floor; release is stricter: keep the stats-off largest internal
block at least 16 KiB and target 20 KiB through the soak. Every expected realtime
task must retain at least 2 KiB stack, heap minima must plateau after warm-up,
and counter deltas for audio/wake drop, decode/write failure and watchdog must
remain zero.

The runtime-stats overlay samples at five-second intervals and adds FreeRTOS
trace/TCB overhead. Use it for per-core/per-task CPU and stack evidence only;
never use its heap result as the final release gate. Compare 240 MHz only after
the same workload passes all RAM/stack/drop gates at 160 MHz.
