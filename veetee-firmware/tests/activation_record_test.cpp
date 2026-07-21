#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>

#include "settings/activation_record.h"

namespace {

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    using veetee::settings::ActivationRecord;
    using veetee::settings::ActivationRecordState;
    using veetee::settings::IsValidActivationRecord;
    using veetee::settings::SealActivationRecord;

    ActivationRecord pending{};
    pending.state = ActivationRecordState::kPending;
    std::snprintf(pending.activation_code, sizeof(pending.activation_code),
                  "482913");
    std::snprintf(pending.activation_challenge,
                  sizeof(pending.activation_challenge),
                  "c3d2a1f0-8b7e-4d6c-9a10-1234567890ab");
    SealActivationRecord(&pending);
    Expect(IsValidActivationRecord(pending), "valid pending record");

    ActivationRecord corrupt = pending;
    corrupt.activation_code[0] = '9';
    Expect(!IsValidActivationRecord(corrupt), "CRC detects changed record");

    ActivationRecord malformed = pending;
    std::snprintf(malformed.activation_code, sizeof(malformed.activation_code),
                  "12A456");
    SealActivationRecord(&malformed);
    Expect(!IsValidActivationRecord(malformed), "non-numeric code rejected");

    ActivationRecord unterminated = pending;
    std::memset(unterminated.activation_challenge, 'x',
                sizeof(unterminated.activation_challenge));
    SealActivationRecord(&unterminated);
    Expect(!IsValidActivationRecord(unterminated),
           "unterminated challenge rejected");

    ActivationRecord active{};
    active.state = ActivationRecordState::kActive;
    std::snprintf(active.device_id, sizeof(active.device_id), "01JDEVICE");
    std::snprintf(active.device_token, sizeof(active.device_token),
                  "0123456789abcdef0123456789abcdef0123456789abcdef");
    std::snprintf(active.websocket_url, sizeof(active.websocket_url),
                  "ws://192.168.1.20:8000/veetee/v1/");
    active.config_version = 7;
    SealActivationRecord(&active);
    Expect(IsValidActivationRecord(active), "valid active record");

    std::cout << "activation record tests passed\n";
    return 0;
}
