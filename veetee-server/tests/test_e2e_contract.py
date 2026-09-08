import unittest

from test_e2e import E2EFailure, E2ETrace, validate_trace


def complete_trace():
    trace = E2ETrace(session_id="session", stt_text="Hà Nội", received_audio_frames=2)
    for index, name in enumerate((
        "speech_audio_end_sent",
        "listen_stop_sent",
        "stt_final_received",
        "vad_speech_ended_received",
        "tts_start_received",
        "first_clause_received",
        "first_binary_received",
        "tts_stop_received",
    )):
        trace.marks[name] = float(index)
    return trace


class E2EContractTests(unittest.TestCase):
    def test_complete_trace_passes(self):
        validate_trace(complete_trace())

    def test_missing_audio_or_marker_fails(self):
        trace = complete_trace()
        trace.received_audio_frames = 0
        with self.assertRaises(E2EFailure):
            validate_trace(trace)

        trace = complete_trace()
        del trace.marks["tts_stop_received"]
        with self.assertRaises(E2EFailure):
            validate_trace(trace)

    def test_wrong_protocol_order_fails(self):
        trace = complete_trace()
        trace.marks["first_binary_received"] = trace.marks["tts_stop_received"] + 1.0
        with self.assertRaises(E2EFailure):
            validate_trace(trace)


if __name__ == "__main__":
    unittest.main()
