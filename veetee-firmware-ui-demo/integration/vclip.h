// Reference VTCLIP1 decoder for the ESP32-S3 side of the Companion pipeline.
//
// This file is part of the standalone UI demo, not of the firmware build. It is
// written so it can be dropped into veetee-firmware/main/display/ unchanged
// once the container is allowlisted, but shipping it requires an explicit,
// versioned ABI change (see ../README.md, "Điều kiện tích hợp"):
//
//   - UI Pack member allowlist gains `clips/*.vclip`;
//   - `ui_abi` moves 1 -> 2 with the compatibility gate updated;
//   - the parser rejects clips whose width/height exceed the panel.
//
// The decoder is allocation-free, bounds-checked on every span and never trusts
// the header against the actual buffer length. Pixels are stored little-endian
// RGB565; the existing flush path is responsible for the panel byte swap.

#ifndef VEETEE_VCLIP_H_
#define VEETEE_VCLIP_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define VCLIP_HEADER_BYTES 32u
#define VCLIP_MAX_FRAMES 65535u

// flags bit 0: frame 0 is a keyframe and every later frame is encoded as a
// delta against the frame before it. Delta frames patch the destination in
// place, so the caller must not clear the region between frames — which is also
// what keeps the SPI flush down to the pixels that actually changed.
#define VCLIP_FLAG_DELTA 0x1u
#define VCLIP_KNOWN_FLAGS VCLIP_FLAG_DELTA

typedef struct {
    uint16_t width;
    uint16_t height;
    uint16_t frame_count;
    uint16_t fps;
    bool delta;
    const uint8_t *index;        // frame_count little-endian uint32 offsets
    const uint8_t *payload;
    uint32_t payload_size;
    uint32_t payload_crc;
} vclip_t;

// Validates magic, reserved fields, declared sizes against `size`, and that the
// frame index is strictly increasing and inside the payload. Does not hash.
bool vclip_open(const uint8_t *bytes, size_t size, vclip_t *out);

// CRC-32/ISO-HDLC over the payload. Run once at apply time, not per frame.
bool vclip_verify_crc(const vclip_t *clip);

// Decodes one frame into `dst` at (x, y). Returns false on any malformed span,
// out-of-range frame, or destination overflow, leaving `dst` partially written.
//
// For a delta clip, frame N > 0 requires `dst` to already hold frame N-1 at the
// same position: play sequentially from frame 0 and never clear in between.
// Seeking is only free to frame 0; any other target must be rebuilt from there.
bool vclip_blit(const vclip_t *clip, uint16_t frame, uint16_t *dst,
                uint16_t dst_width, uint16_t dst_height, uint16_t x, uint16_t y);

// Convenience wrapper for a full-panel frame: dst must hold width * height.
bool vclip_decode_frame(const vclip_t *clip, uint16_t frame, uint16_t *dst,
                        size_t dst_pixels);

#ifdef __cplusplus
}  // extern "C"
#endif

#endif  // VEETEE_VCLIP_H_
