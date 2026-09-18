import { describe, expect, it } from 'vitest'
import {
  createStreamingPcm16Resampler,
  extractOpusPacket,
  resampleFloatToPcm16,
} from './voiceAudio'

describe('voiceAudio protocol framing', () => {
  it('extracts V1, V2 and V3 payloads and rejects truncated frames', () => {
    const v1 = Uint8Array.from([1, 2, 3]).buffer
    expect([...extractOpusPacket(v1, 1)]).toEqual([1, 2, 3])

    const v2 = new ArrayBuffer(19)
    const v2View = new DataView(v2)
    v2View.setUint32(12, 3, false)
    new Uint8Array(v2).set([4, 5, 6], 16)
    expect([...extractOpusPacket(v2, 2)]).toEqual([4, 5, 6])

    const v3 = new ArrayBuffer(7)
    const v3View = new DataView(v3)
    v3View.setUint16(2, 3, false)
    new Uint8Array(v3).set([7, 8, 9], 4)
    expect([...extractOpusPacket(v3, 3)]).toEqual([7, 8, 9])

    const bad = new ArrayBuffer(17)
    new DataView(bad).setUint32(12, 4, false)
    expect(() => extractOpusPacket(bad, 2)).toThrow('truncated')
  })
})

describe('voiceAudio PCM conversion', () => {
  it('clamps float samples to signed PCM16', () => {
    const result = resampleFloatToPcm16(Float32Array.from([-2, -1, 0, 1, 2]), 16000)
    expect([...result]).toEqual([-32768, -32768, 0, 32767, 32767])
  })

  it('keeps streaming state across chunks and can reset', () => {
    const resampler = createStreamingPcm16Resampler(16000)
    const first = resampler.process(Float32Array.from([0, 0.5]), 16000)
    const second = resampler.process(Float32Array.from([-0.5, 1]), 16000)
    expect([...first]).toEqual([0, 16383])
    expect([...second]).toEqual([-16384, 32767])

    resampler.reset()
    expect([...resampler.process(Float32Array.from([1]), 16000)]).toEqual([32767])
  })

  it('downsamples a higher source rate deterministically', () => {
    const result = resampleFloatToPcm16(Float32Array.from([0, 0.25, 0.5, 0.75]), 32000, 16000)
    expect([...result]).toEqual([0, 16383])
  })
})
