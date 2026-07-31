#include "vclip.h"

static const uint8_t kMagic[8] = {'V', 'T', 'C', 'L', 'I', 'P', '1', 0};

static uint16_t read_u16(const uint8_t *bytes) {
    return (uint16_t)(bytes[0] | ((uint16_t)bytes[1] << 8));
}

static uint32_t read_u32(const uint8_t *bytes) {
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}

bool vclip_open(const uint8_t *bytes, size_t size, vclip_t *out) {
    if (bytes == NULL || out == NULL || size < VCLIP_HEADER_BYTES) return false;
    for (size_t index = 0; index < sizeof(kMagic); ++index) {
        if (bytes[index] != kMagic[index]) return false;
    }

    const uint16_t width = read_u16(bytes + 8);
    const uint16_t height = read_u16(bytes + 10);
    const uint16_t frame_count = read_u16(bytes + 12);
    const uint16_t fps = read_u16(bytes + 14);
    const uint32_t flags = read_u32(bytes + 16);
    const uint32_t payload_size = read_u32(bytes + 20);
    const uint32_t payload_crc = read_u32(bytes + 24);
    const uint32_t reserved = read_u32(bytes + 28);

    if ((flags & ~VCLIP_KNOWN_FLAGS) != 0u || reserved != 0u) return false;
    if (width == 0u || height == 0u || frame_count == 0u) return false;
    if (fps == 0u || fps > 60u) return false;
    if ((uint32_t)width * (uint32_t)height > 0x00FFFFFFu) return false;

    const size_t index_bytes = (size_t)frame_count * 4u;
    if (size < VCLIP_HEADER_BYTES + index_bytes) return false;
    if (size - VCLIP_HEADER_BYTES - index_bytes != (size_t)payload_size) return false;

    const uint8_t *index_table = bytes + VCLIP_HEADER_BYTES;
    uint32_t previous = 0u;
    for (uint16_t frame = 0; frame < frame_count; ++frame) {
        const uint32_t offset = read_u32(index_table + (size_t)frame * 4u);
        if (offset >= payload_size) return false;
        if (frame > 0u && offset <= previous) return false;
        if (frame == 0u && offset != 0u) return false;
        previous = offset;
    }

    out->width = width;
    out->height = height;
    out->frame_count = frame_count;
    out->fps = fps;
    out->delta = (flags & VCLIP_FLAG_DELTA) != 0u;
    out->index = index_table;
    out->payload = index_table + index_bytes;
    out->payload_size = payload_size;
    out->payload_crc = payload_crc;
    return true;
}

bool vclip_verify_crc(const vclip_t *clip) {
    if (clip == NULL || clip->payload == NULL) return false;
    uint32_t crc = 0xFFFFFFFFu;
    for (uint32_t index = 0; index < clip->payload_size; ++index) {
        crc ^= clip->payload[index];
        for (int bit = 0; bit < 8; ++bit) {
            crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1u)));
        }
    }
    return (crc ^ 0xFFFFFFFFu) == clip->payload_crc;
}

bool vclip_blit(const vclip_t *clip, uint16_t frame, uint16_t *dst,
                uint16_t dst_width, uint16_t dst_height, uint16_t x, uint16_t y) {
    if (clip == NULL || dst == NULL || frame >= clip->frame_count) return false;
    if ((uint32_t)x + clip->width > dst_width) return false;
    if ((uint32_t)y + clip->height > dst_height) return false;

    const uint32_t start = read_u32(clip->index + (size_t)frame * 4u);
    const uint32_t end = (frame + 1u < clip->frame_count)
                             ? read_u32(clip->index + ((size_t)frame + 1u) * 4u)
                             : clip->payload_size;
    if (end <= start || end > clip->payload_size) return false;

    const uint8_t *read = clip->payload + start;
    const uint8_t *limit = clip->payload + end;
    uint16_t *row = dst + (size_t)y * dst_width + x;
    uint16_t column = 0;
    uint16_t line = 0;

    // Delta ops are only legal after the keyframe; frame 0 always uses the
    // independent encoding so a state change can start playing immediately.
    const bool delta = clip->delta && frame > 0u;

    enum { OP_LITERAL, OP_RUN, OP_SKIP };

    while (line < clip->height) {
        if (read >= limit) return false;
        const uint8_t op = *read++;
        uint16_t remaining;
        uint16_t value = 0;
        int kind;

        if (delta && op == 0xFFu) {
            if ((size_t)(limit - read) < 2u) return false;
            remaining = read_u16(read);
            read += 2;
            if (remaining == 0u) return false;
            kind = OP_SKIP;
        } else if (delta && op >= 0xC0u) {
            remaining = (uint16_t)((op & 0x3Fu) + 1u);
            kind = OP_SKIP;
        } else if ((op & 0x80u) != 0u) {
            remaining = delta ? (uint16_t)((op & 0x3Fu) + 1u) : (uint16_t)((op & 0x7Fu) + 1u);
            if ((size_t)(limit - read) < 2u) return false;
            value = read_u16(read);
            read += 2;
            kind = OP_RUN;
        } else {
            remaining = (uint16_t)(op + 1u);
            if ((size_t)(limit - read) < (size_t)remaining * 2u) return false;
            kind = OP_LITERAL;
        }

        while (remaining > 0u) {
            if (line >= clip->height) return false;
            uint16_t span = (uint16_t)(clip->width - column);
            if (span > remaining) span = remaining;
            if (kind == OP_LITERAL) {
                for (uint16_t step = 0; step < span; ++step) {
                    row[column + step] = read_u16(read);
                    read += 2;
                }
            } else if (kind == OP_RUN) {
                for (uint16_t step = 0; step < span; ++step) {
                    row[column + step] = value;
                }
            }
            // OP_SKIP leaves the destination untouched: it already holds the
            // previous frame, which is exactly what the delta encodes against.
            column = (uint16_t)(column + span);
            remaining = (uint16_t)(remaining - span);
            if (column == clip->width) {
                column = 0;
                ++line;
                row += dst_width;
            }
        }
    }

    // A well-formed frame consumes its whole span range exactly.
    return read == limit;
}

bool vclip_decode_frame(const vclip_t *clip, uint16_t frame, uint16_t *dst,
                        size_t dst_pixels) {
    if (clip == NULL) return false;
    if (dst_pixels < (size_t)clip->width * (size_t)clip->height) return false;
    return vclip_blit(clip, frame, dst, clip->width, clip->height, 0, 0);
}
