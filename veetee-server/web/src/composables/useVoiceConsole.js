import { nextTick, onBeforeUnmount, ref } from 'vue'
import {
  createMicProcessor,
  createStreamingPcm16Resampler,
  extractOpusPacket,
  pcm16LeBytesToFloat32,
  resampleFloatToPcm16,
} from '../lib/voiceAudio'

const INPUT_SAMPLE_RATE = 16000
const DEFAULT_OUTPUT_SAMPLE_RATE = 24000

export function useVoiceConsole({ authRequired, notify, health }) {
  const chatInput = ref('')
  const chatMessages = ref([])
  const protocolLog = ref([])
  const wsState = ref('disconnected')
  const protocolVersion = ref(1)
  const listenMode = ref('auto')
  const rawProtocol = ref('{"type":"chat","text":"Xin chào từ raw protocol"}')
  const useRealAudio = ref(false)
  const isMicRecording = ref(false)
  const isSyntheticAudioRunning = ref(false)
  const audioStatus = ref('Chưa kết nối')
  const firstAudioLatency = ref(null)
  const currentEmotion = ref('neutral')
  const chatScroll = ref(null)
  const pipelineVad = ref('Chờ audio')
  const pipelineAsr = ref('Chờ audio')
  const pipelineLlm = ref('Chờ transcript')
  const pipelineTts = ref('Chờ LLM')

  let ws = null
  let audioCtx = null
  let nextPlayTime = 0
  let turnStartedAt = 0
  let micStream = null
  let micCtx = null
  let micSource = null
  let micProcessor = null
  let micMute = null
  const micResampler = createStreamingPcm16Resampler(INPUT_SAMPLE_RATE)
  let outputSampleRate = DEFAULT_OUTPUT_SAMPLE_RATE
  let syntheticCancelled = false
  let activeSyntheticSource = null
  const activeSources = new Set()

  const wsConnected = () => wsState.value === 'connected'

  function addProtocol(direction, payload) {
    const text = typeof payload === 'string' ? payload : JSON.stringify(payload)
    protocolLog.value.push(`${new Date().toLocaleTimeString()}  ${direction}  ${text}`)
    if (protocolLog.value.length > 220) protocolLog.value.shift()
  }

  function resetPipeline() {
    pipelineVad.value = 'Chờ audio'
    pipelineAsr.value = 'Chờ audio'
    pipelineLlm.value = 'Chờ transcript'
    pipelineTts.value = 'Chờ LLM'
  }

  async function ensureAudio({ reportBlocked = true, prime = false } = {}) {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext
    if (!AudioContextCtor) throw new Error('Trình duyệt không hỗ trợ Web Audio')
    if (!audioCtx || audioCtx.state === 'closed') audioCtx = new AudioContextCtor()

    if (audioCtx.state !== 'running') {
      try {
        await audioCtx.resume()
      } catch (error) {
        if (reportBlocked) {
          audioStatus.value = 'Audio bị trình duyệt chặn'
          addProtocol('AUDIO ERR', `AudioContext resume failed: ${error.message || error}`)
        }
      }
    }

    // Some browsers require an actual source.start() during a user gesture,
    // not only AudioContext.resume(), before allowing later streamed playback.
    if (prime && audioCtx.state === 'running') {
      try {
        const unlockBuffer = audioCtx.createBuffer(
          1,
          1,
          audioCtx.sampleRate || DEFAULT_OUTPUT_SAMPLE_RATE,
        )
        const unlockSource = audioCtx.createBufferSource()
        unlockSource.buffer = unlockBuffer
        unlockSource.connect(audioCtx.destination)
        unlockSource.start()
      } catch (error) {
        addProtocol('AUDIO WARN', `Output prime failed: ${error.message || error}`)
      }
    }

    if (audioCtx.state !== 'running' && reportBlocked) {
      audioStatus.value = 'Audio bị trình duyệt chặn · bấm “Bật âm thanh”'
    }
    return audioCtx.state === 'running'
  }

  async function unlockAudio() {
    try {
      const running = await ensureAudio({ prime: true })
      if (!running) {
        notify('Trình duyệt vẫn đang chặn audio. Kiểm tra tab/site sound rồi bấm lại.', 'warning')
        return false
      }
      audioStatus.value = 'Audio sẵn sàng'
      addProtocol('AUDIO ✓', `AudioContext running · output ${audioCtx.sampleRate || 'auto'} Hz`)
      return true
    } catch (error) {
      const message = error.message || String(error)
      audioStatus.value = `Lỗi audio: ${message}`
      addProtocol('AUDIO ERR', message)
      notify(message, 'error')
      return false
    }
  }

  function extractAudioPayload(buffer) {
    return extractOpusPacket(buffer, protocolVersion.value)
  }

  function playAudioSamples(samples, sampleRate = DEFAULT_OUTPUT_SAMPLE_RATE) {
    if (!samples?.length) throw new Error('PCM TTS rỗng')
    if (!audioCtx) throw new Error('AudioContext chưa khởi tạo')
    if (audioCtx.state !== 'running') throw new Error(`AudioContext đang ${audioCtx.state}`)

    const rate = Number(sampleRate) || DEFAULT_OUTPUT_SAMPLE_RATE
    const buffer = audioCtx.createBuffer(1, samples.length, rate)
    buffer.getChannelData(0).set(samples)

    const source = audioCtx.createBufferSource()
    source.buffer = buffer
    source.connect(audioCtx.destination)

    const now = audioCtx.currentTime
    if (!Number.isFinite(nextPlayTime) || nextPlayTime < now) nextPlayTime = now + 0.035
    const scheduledAt = nextPlayTime
    source.start(scheduledAt)
    nextPlayTime = scheduledAt + buffer.duration

    activeSources.add(source)
    source.onended = () => activeSources.delete(source)
    audioStatus.value = `Đang phát TTS · ${rate / 1000} kHz`
    return buffer.duration
  }

  function sendWs(payload, label = '→') {
    if (!wsConnected() || !ws) return false
    const text = typeof payload === 'string' ? payload : JSON.stringify(payload)
    ws.send(text)
    addProtocol(label, text)
    return true
  }

  async function scrollChat() {
    await nextTick()
    if (chatScroll.value) chatScroll.value.scrollTop = chatScroll.value.scrollHeight
  }

  function handleTextMessage(data) {
    if (data.type === 'hello') {
      const negotiatedRate = Number(data.audio_params?.sample_rate)
      if (Number.isFinite(negotiatedRate) && negotiatedRate >= 8000 && negotiatedRate <= 96000) {
        outputSampleRate = negotiatedRate
        addProtocol('AUDIO ✓', `Negotiated output ${outputSampleRate} Hz`)
      }
      return
    }

    if (data.type === 'vad') {
      if (data.state === 'speech_started') {
        pipelineVad.value = 'Có giọng nói'
        pipelineAsr.value = 'Đang nhận âm thanh…'
      } else if (data.state === 'speech_ended') {
        pipelineVad.value = 'Kết thúc câu'
      }
      return
    }

    if (data.type === 'stt' && data.text) {
      pipelineAsr.value = data.speech_final || data.is_final ? `✓ ${data.text}` : `… ${data.text}`
      if (data.speech_final || data.is_final) {
        chatMessages.value.push({ role: 'user', text: data.text })
        pipelineLlm.value = 'Đang suy nghĩ…'
        turnStartedAt = performance.now()
        firstAudioLatency.value = null
      }
      audioStatus.value = `ASR: ${data.text}`
      return
    }

    if (data.type === 'llm') {
      pipelineLlm.value = 'Đã bắt đầu trả lời'
      pipelineTts.value = 'Đang tổng hợp…'
      if (data.emotion) currentEmotion.value = data.emotion
      return
    }

    if (data.type === 'tts') {
      if (data.state === 'start') {
        pipelineTts.value = 'Đang tổng hợp…'
        chatMessages.value.push({ role: 'assistant', text: '' })
        nextPlayTime = audioCtx?.currentTime || 0
      } else if (data.state === 'sentence_start') {
        const last = [...chatMessages.value].reverse().find(item => item.role === 'assistant')
        if (last) last.text += `${last.text ? ' ' : ''}${data.text || ''}`
      } else if (data.state === 'stop') {
        pipelineTts.value = 'Hoàn tất'
        const remainingMs = audioCtx
          ? Math.max(0, Math.round((nextPlayTime - audioCtx.currentTime) * 1000))
          : 0
        audioStatus.value = remainingMs > 0
          ? `TTS hoàn tất · còn ${remainingMs} ms đang phát`
          : 'TTS hoàn tất'
      }
    }
  }

  async function connectWs() {
    if (wsConnected() && ws) {
      ws.close(1000, 'browser_disconnect')
      return
    }
    await ensureAudio({ prime: true })
    wsState.value = 'connecting'
    outputSampleRate = DEFAULT_OUTPUT_SAMPLE_RATE
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${scheme}//${location.host}/ws`
    addProtocol('INFO', `Connecting ${url}`)
    ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    ws.onopen = () => {
      wsState.value = 'connected'
      audioStatus.value = 'Đã kết nối WebSocket'
      resetPipeline()
      sendWs({
        type: 'hello',
        version: Number(protocolVersion.value),
        transport: 'websocket',
        audio_params: {
          format: 'pcm16',
          sample_rate: INPUT_SAMPLE_RATE,
          channels: 1,
          frame_duration: 20,
          output_format: 'pcm16',
        },
      }, 'HELLO →')
    }
    ws.onmessage = async event => {
      if (typeof event.data === 'string') {
        addProtocol('WS ←', event.data)
        let data
        try { data = JSON.parse(event.data) } catch (_) { return }
        handleTextMessage(data)
        await scrollChat()
      } else if (event.data instanceof ArrayBuffer) {
        try {
          await ensureAudio()
          if (turnStartedAt && !firstAudioLatency.value) {
            firstAudioLatency.value = Math.round(performance.now() - turnStartedAt)
            pipelineTts.value = `Audio đầu ${firstAudioLatency.value} ms`
          }
          const raw = extractAudioPayload(event.data)
          const samples = pcm16LeBytesToFloat32(raw)
          addProtocol('WS ← BIN', `${event.data.byteLength} bytes · pcm16 ${raw.byteLength} bytes`)
          const duration = playAudioSamples(samples, outputSampleRate)
          addProtocol(
            'AUDIO ▶',
            `${samples.length} samples · ${outputSampleRate} Hz · ${Math.round(duration * 1000)} ms`,
          )
        } catch (error) {
          const message = error.message || String(error)
          audioStatus.value = `Lỗi phát TTS: ${message}`
          pipelineTts.value = 'Lỗi playback'
          addProtocol('AUDIO ERR', message)
        }
      }
    }
    ws.onclose = event => {
      addProtocol('WS CLOSED', `code=${event.code}${event.reason ? ` reason=${event.reason}` : ''}`)
      wsState.value = 'disconnected'
      audioStatus.value = 'Đã ngắt WebSocket'
      ws = null
      stopMic(false)
    }
    ws.onerror = () => {
      addProtocol('WS ERR', 'WebSocket error')
      wsState.value = 'error'
      audioStatus.value = 'Lỗi WebSocket'
    }
  }

  async function reconnectWs() {
    const current = ws
    if (current && current.readyState !== WebSocket.CLOSED) {
      current.close(1000, 'browser_reconnect')
      const deadline = performance.now() + 1500
      while (current.readyState !== WebSocket.CLOSED && performance.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 25))
      }
    }
    ws = null
    wsState.value = 'disconnected'
    await connectWs()
  }

  async function sendChat() {
    const text = chatInput.value.trim()
    if (!text || !wsConnected()) return
    await ensureAudio({ prime: true })
    const degradedReasons = health?.value?.degraded_reasons || []
    if (degradedReasons.includes('llm_not_warm')) {
      pipelineLlm.value = 'LLM chưa sẵn sàng'
      pipelineTts.value = 'Chờ LLM'
      notify('LLM chưa sẵn sàng. Hãy cấu hình Groq API key trong Runtime; cấu hình mới sẽ được áp dụng ngay.', 'warning')
      return
    }
    chatInput.value = ''
    if (useRealAudio.value) {
      await runTextThroughRealAudio(text)
      return
    }
    chatMessages.value.push({ role: 'user', text })
    firstAudioLatency.value = null
    turnStartedAt = performance.now()
    pipelineVad.value = 'Bỏ qua (text)'
    pipelineAsr.value = 'Bỏ qua (text)'
    pipelineLlm.value = 'Đang suy nghĩ…'
    pipelineTts.value = 'Chờ LLM'
    sendWs({ type: 'chat', text }, 'CHAT →')
    await scrollChat()
  }

  function abortTurn() {
    syntheticCancelled = true
    if (activeSyntheticSource) {
      try { activeSyntheticSource.stop() } catch (_) {}
      activeSyntheticSource = null
    }
    sendWs({ type: 'abort', reason: 'user_cancel' }, 'ABORT →')
    for (const source of activeSources) {
      try { source.stop() } catch (_) {}
    }
    activeSources.clear()
    if (audioCtx) nextPlayTime = audioCtx.currentTime
    audioStatus.value = 'Đã ngắt lượt hiện tại'
  }

  async function startMic() {
    if (!wsConnected() || !navigator.mediaDevices?.getUserMedia) return
    await ensureAudio({ prime: true })
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      })
      micCtx = new (window.AudioContext || window.webkitAudioContext)()
      if (micCtx.state === 'suspended') await micCtx.resume()
      micResampler.reset()
      micSource = micCtx.createMediaStreamSource(micStream)
      const processor = await createMicProcessor(micCtx)
      micProcessor = processor.node
      micMute = micCtx.createGain()
      micMute.gain.value = 0
      micSource.connect(micProcessor)
      micProcessor.connect(micMute)
      micMute.connect(micCtx.destination)
      const forwardSamples = mono => {
        if (!isMicRecording.value || !wsConnected()) return
        const pcm = micResampler.process(mono, micCtx.sampleRate)
        if (pcm.length) ws.send(pcm.buffer.slice(pcm.byteOffset, pcm.byteOffset + pcm.byteLength))
      }
      if (processor.legacy) {
        micProcessor.onaudioprocess = event => forwardSamples(event.inputBuffer.getChannelData(0))
      } else {
        micProcessor.port.onmessage = event => forwardSamples(event.data)
      }
      isMicRecording.value = true
      resetPipeline()
      sendWs({ type: 'listen', state: 'start', mode: listenMode.value }, 'LISTEN →')
      audioStatus.value = `Mic ${micCtx.sampleRate} Hz → PCM16 16 kHz · ${processor.legacy ? 'legacy' : 'AudioWorklet'}`
    } catch (error) {
      notify(`Không mở được mic: ${error.message}`, 'error')
      await stopMic(false)
    }
  }

  async function stopMic(sendStop = true) {
    const wasActive = isMicRecording.value || micStream
    isMicRecording.value = false
    if (!wasActive) return
    if (micProcessor) {
      micProcessor.onaudioprocess = null
      if (micProcessor.port) micProcessor.port.onmessage = null
      try { micProcessor.disconnect() } catch (_) {}
    }
    if (micSource) try { micSource.disconnect() } catch (_) {}
    if (micMute) try { micMute.disconnect() } catch (_) {}
    micStream?.getTracks().forEach(track => track.stop())
    if (micCtx && micCtx.state !== 'closed') try { await micCtx.close() } catch (_) {}
    micStream = micCtx = micSource = micProcessor = micMute = null
    if (sendStop && wsConnected()) sendWs({ type: 'listen', state: 'stop' }, 'LISTEN →')
    audioStatus.value = 'Mic đã dừng'
  }

  async function toggleMic() {
    if (isMicRecording.value) await stopMic(true)
    else await startMic()
  }

  async function managementVoiceRequest(text) {
    const response = await fetch('/api/test-voice', {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (response.status === 401) {
      authRequired.value = true
      throw new Error('Cần đăng nhập quản trị')
    }
    if (!response.ok) {
      let message = `HTTP ${response.status}`
      try {
        const data = await response.json()
        if (data?.code === 'TTS_PREEMPTED') {
          message = 'TTS thử đã nhường GPU cho cuộc hội thoại trực tiếp. Hãy thử lại sau khi VeeTee nói xong.'
        } else if (data?.error) {
          message = data.error
        }
      } catch (_) {}
      throw new Error(message)
    }
    return response
  }

  async function directTts() {
    const text = chatInput.value.trim() || 'Xin chào, đây là bài kiểm tra giọng nói VeeTee.'
    try {
      await ensureAudio({ prime: true })
      const response = await managementVoiceRequest(text)
      addProtocol('TTS → HTTP', text)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audio.onended = () => URL.revokeObjectURL(url)
      await audio.play()
      addProtocol('TTS ← HTTP', `${blob.size} bytes`)
      notify('Đang phát TTS trực tiếp', 'success')
    } catch (error) {
      if (error.message !== 'Cần đăng nhập quản trị') notify(error.message, 'error')
    }
  }

  async function runTextThroughRealAudio(text) {
    if (!wsConnected() || isSyntheticAudioRunning.value || isMicRecording.value) return
    isSyntheticAudioRunning.value = true
    syntheticCancelled = false
    resetPipeline()
    audioStatus.value = 'VieNeu đang tạo audio test…'
    try {
      await ensureAudio({ prime: true })
      const response = await managementVoiceRequest(text)
      const sourceBytes = await response.arrayBuffer()
      const decodeCtx = new (window.AudioContext || window.webkitAudioContext)()
      let decoded
      try { decoded = await decodeCtx.decodeAudioData(sourceBytes.slice(0)) } finally { /* closed below after copy */ }
      const mono = new Float32Array(decoded.length)
      for (let channel = 0; channel < decoded.numberOfChannels; channel += 1) {
        const data = decoded.getChannelData(channel)
        for (let i = 0; i < data.length; i += 1) mono[i] += data[i] / decoded.numberOfChannels
      }
      const pcm = resampleFloatToPcm16(mono, decoded.sampleRate)
      const playback = audioCtx.createBuffer(1, decoded.length, decoded.sampleRate)
      playback.getChannelData(0).set(mono)
      await decodeCtx.close()

      activeSyntheticSource = audioCtx.createBufferSource()
      activeSyntheticSource.buffer = playback
      activeSyntheticSource.connect(audioCtx.destination)
      activeSyntheticSource.onended = () => { activeSyntheticSource = null }
      activeSyntheticSource.start()

      sendWs({ type: 'listen', state: 'start', mode: listenMode.value }, 'LISTEN →')
      audioStatus.value = 'VieNeu → PCM16 → VAD → ASR → LLM → TTS'
      const chunkSamples = 320
      for (let offset = 0; offset < pcm.length && !syntheticCancelled; offset += chunkSamples) {
        const chunk = pcm.subarray(offset, Math.min(offset + chunkSamples, pcm.length))
        if (!wsConnected()) break
        ws.send(chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength))
        await new Promise(resolve => setTimeout(resolve, 20))
      }
      if (wsConnected()) sendWs({ type: 'listen', state: 'stop' }, 'LISTEN →')
      audioStatus.value = syntheticCancelled ? 'Đã dừng audio test' : 'Đã gửi hết audio, chờ ASR chốt câu…'
    } catch (error) {
      if (error.message !== 'Cần đăng nhập quản trị') notify(`Audio pipeline: ${error.message}`, 'error')
      audioStatus.value = `Lỗi audio test: ${error.message}`
    } finally {
      isSyntheticAudioRunning.value = false
    }
  }

  function listenStart() {
    resetPipeline()
    sendWs({ type: 'listen', state: 'start', mode: listenMode.value }, 'LISTEN →')
  }

  function listenStop() {
    sendWs({ type: 'listen', state: 'stop' }, 'LISTEN →')
  }

  async function wakeDetect(andStart = false) {
    const text = chatInput.value.trim()
    if (!text) {
      addProtocol('WAKE ERR', 'Nhập wake/detect text trong ô chat trước')
      return
    }
    if (!sendWs({ type: 'listen', state: 'detect', text }, 'WAKE →')) return
    if (andStart) {
      await new Promise(resolve => setTimeout(resolve, 50))
      listenStart()
    }
  }

  function sendEndIntent() {
    const text = chatInput.value.trim()
    if (!text) {
      addProtocol('END ERR', 'Nhập yêu cầu kết thúc vào ô chat trước')
      return
    }
    sendWs({ type: 'chat', text }, 'END →')
  }

  function sendRawProtocol() {
    const raw = rawProtocol.value.trim()
    if (!raw) return
    try {
      sendWs(JSON.parse(raw), 'RAW →')
    } catch (error) {
      addProtocol('RAW ERR', `JSON không hợp lệ: ${error.message}`)
    }
  }

  async function runHttpDiagnostic(label, url) {
    try {
      const response = await fetch(url, { cache: 'no-store', credentials: 'same-origin' })
      const text = await response.text()
      addProtocol(`HTTP ${label}`, `${response.status} ${text.slice(0, 1200)}`)
    } catch (error) {
      addProtocol(`HTTP ${label} ERR`, error.message)
    }
  }

  async function testHealth() { await runHttpDiagnostic('HEALTH', '/health') }
  async function testOta() { await runHttpDiagnostic('OTA', '/ota/') }

  function clearConversation() {
    chatMessages.value = []
    protocolLog.value = []
    firstAudioLatency.value = null
    currentEmotion.value = 'neutral'
    resetPipeline()
  }

  async function dispose() {
    syntheticCancelled = true
    if (activeSyntheticSource) try { activeSyntheticSource.stop() } catch (_) {}
    if (ws) try { ws.close() } catch (_) {}
    await stopMic(false)
    if (audioCtx && audioCtx.state !== 'closed') await audioCtx.close()
  }

  onBeforeUnmount(dispose)

  return {
    chatInput, chatMessages, protocolLog, wsState, protocolVersion, listenMode, rawProtocol,
    useRealAudio, isMicRecording, isSyntheticAudioRunning, audioStatus, firstAudioLatency,
    currentEmotion, chatScroll, pipelineVad, pipelineAsr, pipelineLlm, pipelineTts,
    wsConnected, connectWs, reconnectWs, unlockAudio, sendChat, abortTurn, toggleMic, directTts,
    runTextThroughRealAudio, listenStart, listenStop, wakeDetect, sendEndIntent,
    sendRawProtocol, testHealth, testOta, sendWs, clearConversation, dispose,
  }
}
