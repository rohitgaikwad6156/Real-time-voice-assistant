/**
 * Minimal PCM audio player for streaming playback.
 * 
 * Takes raw 16-bit linear PCM little-endian audio buffers (default: 24,000 Hz mono
 * as emitted by Gemini Live API) and schedules them seamlessly on the Web Audio API timeline.
 */

class PcmPlayer {
  /**
   * @param {Object} options
   * @param {number} [options.sampleRate=24000] Output sample rate in Hz.
   */
  constructor(options = {}) {
    this.sampleRate = options.sampleRate || 24000;
    this.audioCtx = null;
    this.nextPlayTime = 0;
  }

  /**
   * Schedule a raw 16-bit PCM chunk for playback.
   * 
   * @param {ArrayBuffer} arrayBuffer Raw PCM 16-bit little-endian audio bytes.
   */
  playChunk(arrayBuffer) {
    if (!arrayBuffer || arrayBuffer.byteLength === 0) return;

    // Lazily initialize AudioContext
    if (!this.audioCtx) {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      this.audioCtx = new AudioContextClass();
    }

    if (this.audioCtx.state === "suspended") {
      this.audioCtx.resume();
    }

    // Convert Int16 little-endian samples to Float32 [-1.0, 1.0]
    const int16Array = new Int16Array(arrayBuffer);
    const float32Array = new Float32Array(int16Array.length);
    for (let i = 0; i < int16Array.length; i++) {
      float32Array[i] = int16Array[i] / 32768.0;
    }

    // Create single-channel AudioBuffer at model sample rate (24 kHz)
    const audioBuffer = this.audioCtx.createBuffer(1, float32Array.length, this.sampleRate);
    audioBuffer.getChannelData(0).set(float32Array);

    const sourceNode = this.audioCtx.createBufferSource();
    sourceNode.buffer = audioBuffer;
    sourceNode.connect(this.audioCtx.destination);

    // Schedule contiguous playback on timeline
    const currentTime = this.audioCtx.currentTime;
    if (this.nextPlayTime < currentTime) {
      this.nextPlayTime = currentTime;
    }

    sourceNode.start(this.nextPlayTime);
    this.nextPlayTime += audioBuffer.duration;
  }

  /**
   * Reset player timeline and close context.
   */
  stop() {
    this.nextPlayTime = 0;
    if (this.audioCtx && this.audioCtx.state !== "closed") {
      this.audioCtx.close().catch(() => {});
      this.audioCtx = null;
    }
  }
}

// Attach to window object for global availability
if (typeof window !== "undefined") {
  window.PcmPlayer = PcmPlayer;
}
