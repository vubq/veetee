#include <cassert>
#include <iostream>

#include "audio/pcm_submission_gate.h"

int main() {
    veetee::audio::PcmSubmissionGate gate;
    assert(gate.TryEnter());
    gate.Close();
    assert(!gate.drained());
    assert(!gate.TryEnter());
    gate.Leave();
    assert(gate.drained());
    assert(!gate.TryEnter());
    gate.Open();
    assert(gate.TryEnter());
    gate.Leave();
    assert(gate.drained());
    std::cout << "pcm_submission_gate_test: passed\n";
    return 0;
}
