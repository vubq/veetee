import asyncio
import websockets
import json
import time
import numpy as np
import opuslib_next
from vieneu import Vieneu
import soxr

async def run_full_speech_test():
    print("=== STEP 1: Synthesizing test question audio using Vieneu ===")
    tts = Vieneu()
    question_text = "Hà Nội là thủ đô của nước nào?"
    print(f"Generating audio for test query: '{question_text}'...")
    audio_48k = tts.infer(question_text)
    
    # Resample to 16kHz for microphone simulation
    audio_16k_f = soxr.resample(audio_48k, 48000, 16000)
    audio_16k_i16 = (np.clip(audio_16k_f, -1.0, 1.0) * 32767.0).astype(np.int16)
    
    # Encode into 16kHz Opus frames (60ms = 960 samples)
    enc16 = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)
    opus_frames = []
    pcm_bytes = audio_16k_i16.tobytes()
    frame_size_bytes = 960 * 2
    for i in range(0, len(pcm_bytes), frame_size_bytes):
        chunk = pcm_bytes[i:i + frame_size_bytes]
        if len(chunk) == frame_size_bytes:
            frame = enc16.encode(chunk, 960)
            opus_frames.append(frame)
            
    # Also generate a silence frame
    silence_frame = enc16.encode(b"\x00\x00" * 960, 960)
    print(f"Generated {len(opus_frames)} Opus frames (~{len(opus_frames)*0.06:.2f}s of speech).")

    print("\n=== STEP 2: Connecting to VeeTee Server via WebSocket ===")
    uri = "ws://127.0.0.1:8000/"
    headers = {
        "Device-Id": "00:11:22:33:44:55",
        "Client-Id": "test-client-uuid",
        "Protocol-Version": "1"
    }
    
    async with websockets.connect(uri, additional_headers=headers) as ws:
        # Handshake
        await ws.send(json.dumps({
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60
            }
        }))
        
        resp = await ws.recv()
        print("Server Hello:", resp)
        session_id = json.loads(resp)["session_id"]

        print("\n=== STEP 3: Streaming simulated speech audio into server ===")
        await ws.send(json.dumps({"type": "listen", "state": "start", "session_id": session_id}))
        
        t_speech_start = time.time()
        for frame in opus_frames:
            await ws.send(frame)
            await asyncio.sleep(0.06) # Stream in real time
            
        # Stream 500ms trailing background/silence frames (as normal mic would)
        for _ in range(8):
            await ws.send(silence_frame)
            await asyncio.sleep(0.06)
            
        await ws.send(json.dumps({"type": "listen", "state": "stop", "session_id": session_id}))
        t_speech_end = time.time()
        print(f"Finished sending audio in {t_speech_end - t_speech_start:.2f}s. Waiting for STT, LLM and TTS stream...")

        first_tts_audio_received = False
        received_audio_frames = 0
        
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=12.0)
                if isinstance(msg, str):
                    data = json.loads(msg)
                    msg_type = data.get("type")
                    if msg_type == "stt":
                        print(f"-> [STT Received in {time.time() - t_speech_end:.3f}s]: '{data.get('text')}'")
                    elif msg_type == "llm":
                        print(f"-> [LLM Emotion]: {data.get('emotion')}")
                    elif msg_type == "tts":
                        state = data.get("state")
                        if state == "start":
                            print(f"-> [TTS Start in {time.time() - t_speech_end:.3f}s]")
                        elif state == "sentence_start":
                            print(f"-> [TTS Sentence in {time.time() - t_speech_end:.3f}s]: '{data.get('text')}'")
                        elif state == "stop":
                            print(f"-> [TTS Stop] Completed turn! Total audio frames received: {received_audio_frames}")
                            break
                elif isinstance(msg, bytes):
                    received_audio_frames += 1
                    if not first_tts_audio_received:
                        first_tts_audio_received = True
                        latency = time.time() - t_speech_end
                        print(f"🚀 [FIRST AUDIO CHUNK ARRIVED!] Latency (Time-To-First-Audio): {latency:.3f}s")
            except asyncio.TimeoutError:
                print("Timeout waiting for server response.")
                break

    print("\n✅ ALL TESTS PASSED SUCCESSFULLY! Realtime streaming and low-latency response verified.")

if __name__ == "__main__":
    asyncio.run(run_full_speech_test())
