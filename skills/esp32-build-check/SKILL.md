---
name: esp32-build-check
description: Validate Veetee ESP32-S3 firmware with host CMake tests and ESP-IDF 6.0.2 builds, and analyze compile failures. Use for changes under veetee-firmware; never flash or monitor without an explicit request.
---

# ESP32 Build Check

## Read first

- `../../docs/03-firmware-spec.md`
- `../../docs/04-protocol-compatibility.md`
- `../../docs/05-realtime-conversation.md` for wake, audio or abort changes.
- `../../veetee-firmware/README.md`
- The closest host test.

## Host validation

```bash
cd ../../veetee-firmware
cmake -S tests -B build/host-tests
cmake --build build/host-tests
ctest --test-dir build/host-tests --output-on-failure
```

Run one target when appropriate:

```bash
ctest --test-dir build/host-tests -R '^state_machine_test$' --output-on-failure
```

## ESP-IDF build

```bash
source /home/vubq/.espressif/v6.0.2/esp-idf/export.sh
cd ../../veetee-firmware
idf.py set-target esp32s3
idf.py build
```

Analyze the first actionable compile/config/link/size error and fix only the owning boundary. Preserve warnings-as-errors, board profile, partition and signed resource/OTA contracts.

Never run `flash`, `monitor`, change target, pin map, partition layout or hardware config unless the user explicitly requests it. Report host tests, ESP-IDF build and physical-board validation as three separate statuses.
