/**
 * Encode one or more decoded AudioBuffers into a single 16-bit PCM WAV Blob.
 *
 * Used by the streaming TTS path (use-tts.ts) to produce a replayable
 * audio URL for the history list. The streaming wire protocol delivers
 * one complete WAV per chunk, but raw concatenation of multi-WAV blobs
 * does not yield a valid WAV — browsers will only see the first header.
 * Decoding to PCM and re-encoding into a single WAV is the cleanest way
 * to give the history a stable audio/wav blob URL.
 *
 * Channel layout: interleaved samples in the WAV `data` chunk.
 * Sample format: little-endian signed 16-bit PCM.
 *
 * All input buffers must share the same sampleRate and channel count
 * (Kokoro emits 24 kHz mono, so this is always true in practice).
 */
export function audioBuffersToWavBlob(buffers: AudioBuffer[]): Blob {
  if (buffers.length === 0) {
    return new Blob([], { type: "audio/wav" });
  }

  const firstBuffer = buffers[0];
  if (!firstBuffer) {
    return new Blob([], { type: "audio/wav" });
  }
  const sampleRate = firstBuffer.sampleRate;
  const numChannels = firstBuffer.numberOfChannels;
  const bitsPerSample = 16;
  const totalFrames = buffers.reduce((sum, b) => sum + b.length, 0);

  const blockAlign = (numChannels * bitsPerSample) / 8;
  const byteRate = sampleRate * blockAlign;
  const dataSize = totalFrames * blockAlign;
  const headerSize = 44;
  const totalSize = headerSize + dataSize;

  const ab = new ArrayBuffer(totalSize);
  const view = new DataView(ab);

  let offset = 0;
  const writeStr = (s: string) => {
    for (let i = 0; i < s.length; i++) {
      view.setUint8(offset++, s.charCodeAt(i));
    }
  };
  const writeU32 = (v: number) => {
    view.setUint32(offset, v, true);
    offset += 4;
  };
  const writeU16 = (v: number) => {
    view.setUint16(offset, v, true);
    offset += 2;
  };

  writeStr("RIFF");
  writeU32(totalSize - 8);
  writeStr("WAVE");
  writeStr("fmt ");
  writeU32(16);
  writeU16(1);
  writeU16(numChannels);
  writeU32(sampleRate);
  writeU32(byteRate);
  writeU16(blockAlign);
  writeU16(bitsPerSample);
  writeStr("data");
  writeU32(dataSize);

  for (const buf of buffers) {
    if (
      buf.sampleRate !== sampleRate ||
      buf.numberOfChannels !== numChannels
    ) {
      throw new Error(
        `audioBuffersToWavBlob: mismatched buffer format ` +
          `(expected ${sampleRate}Hz/${numChannels}ch, ` +
          `got ${buf.sampleRate}Hz/${buf.numberOfChannels}ch)`,
      );
    }
    const channels: Float32Array[] = [];
    for (let c = 0; c < numChannels; c++) {
      channels.push(buf.getChannelData(c));
    }
    for (let i = 0; i < buf.length; i++) {
      for (let c = 0; c < numChannels; c++) {
        const channel = channels[c];
        if (!channel) continue;
        let s = channel[i];
        if (s === undefined) continue;
        if (s > 1) s = 1;
        else if (s < -1) s = -1;
        const intSample = s < 0 ? s * 0x8000 : s * 0x7fff;
        view.setInt16(offset, intSample, true);
        offset += 2;
      }
    }
  }

  return new Blob([ab], { type: "audio/wav" });
}
