export function extractOpusPacket(buffer, protocolVersion = 1) {
  const bytes = new Uint8Array(buffer)
  const view = new DataView(buffer)
  const version = Number(protocolVersion)

  if (version === 2) {
    if (bytes.byteLength < 16) throw new Error('V2 packet invalid')
    const size = view.getUint32(12, false)
    if (size > bytes.byteLength - 16) throw new Error('V2 packet truncated')
    return bytes.slice(16, 16 + size)
  }

  if (version === 3) {
    if (bytes.byteLength < 4) throw new Error('V3 packet invalid')
    const size = view.getUint16(2, false)
    if (size > bytes.byteLength - 4) throw new Error('V3 packet truncated')
    return bytes.slice(4, 4 + size)
  }

  return bytes
}

export function pcm16LeBytesToFloat32(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input || 0)
  if (bytes.byteLength < 2) return new Float32Array(0)
  const sampleCount = Math.floor(bytes.byteLength / 2)
  const view = new DataView(bytes.buffer, bytes.byteOffset, sampleCount * 2)
  const out = new Float32Array(sampleCount)
  for (let index = 0; index < sampleCount; index += 1) {
    const sample = view.getInt16(index * 2, true)
    out[index] = sample < 0 ? sample / 32768 : sample / 32767
  }
  return out
}

export function resampleFloatToPcm16(input, sourceRate, targetRate = 16000) {
  if (!input?.length) return new Int16Array(0)
  const ratio = sourceRate / targetRate
  const length = Math.max(1, Math.floor(input.length / ratio))
  const out = new Int16Array(length)

  for (let i = 0; i < length; i += 1) {
    const position = i * ratio
    const left = Math.floor(position)
    const right = Math.min(input.length - 1, left + 1)
    const fraction = position - left
    const value = Math.max(-1, Math.min(1, input[left] + (input[right] - input[left]) * fraction))
    out[i] = value < 0 ? value * 32768 : value * 32767
  }

  return out
}

export function createStreamingPcm16Resampler(targetRate = 16000) {
  let inputSeen = 0
  let nextSourcePos = 0
  let previousSample = 0
  let hasPreviousSample = false

  function reset() {
    inputSeen = 0
    nextSourcePos = 0
    previousSample = 0
    hasPreviousSample = false
  }

  function process(input, sourceRate) {
    if (!input?.length) return new Int16Array(0)
    if (sourceRate === targetRate) {
      return Int16Array.from(input, value => {
        const bounded = Math.max(-1, Math.min(1, value))
        return bounded < 0 ? bounded * 32768 : bounded * 32767
      })
    }

    const ratio = sourceRate / targetRate
    const start = inputSeen
    const end = start + input.length
    const out = []

    while (nextSourcePos < end) {
      const leftIndex = Math.floor(nextSourcePos)
      const fraction = nextSourcePos - leftIndex
      const rightIndex = fraction > 0 ? leftIndex + 1 : leftIndex
      if (rightIndex >= end) break

      const sampleAt = index => (
        index === start - 1 && hasPreviousSample
          ? previousSample
          : input[index - start]
      )
      const left = sampleAt(leftIndex)
      const right = sampleAt(rightIndex)
      if (!Number.isFinite(left) || !Number.isFinite(right)) break

      const value = Math.max(-1, Math.min(1, left + (right - left) * fraction))
      out.push(value < 0 ? value * 32768 : value * 32767)
      nextSourcePos += ratio
    }

    inputSeen = end
    previousSample = input[input.length - 1]
    hasPreviousSample = true
    return Int16Array.from(out)
  }

  return { process, reset }
}

export async function createMicProcessor(ctx) {
  if (ctx.audioWorklet && typeof window.AudioWorkletNode !== 'undefined') {
    const source = `
      class VeeTeeMicWorklet extends AudioWorkletProcessor {
        process(inputs) {
          const input = inputs[0] && inputs[0][0]
          if (input && input.length) this.port.postMessage(input.slice(0))
          return true
        }
      }
      registerProcessor('veetee-mic-worklet', VeeTeeMicWorklet)
    `
    const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }))
    try {
      await ctx.audioWorklet.addModule(url)
    } finally {
      URL.revokeObjectURL(url)
    }
    return {
      node: new window.AudioWorkletNode(ctx, 'veetee-mic-worklet', {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      }),
      legacy: false,
    }
  }

  return { node: ctx.createScriptProcessor(2048, 1, 1), legacy: true }
}
