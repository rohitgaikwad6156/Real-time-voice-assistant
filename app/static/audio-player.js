/**
 * AudioPlayer: Progressive PCM Audio Streaming Player
 * 
 * Designed for Gemini Live API audio output (24,000 Hz, 16-bit linear PCM little-endian).
 * 
 * Features:
 * - Progressive streaming: plays audio immediately as chunks arrive with zero batch waiting
 * - Strict chunk ordering: schedules sequential chunks contiguously on the AudioContext timeline
 * - Jitter & Underflow compensation: handles network timing variations cleanly
 * - Overlap prevention: guarantees chunk N+1 begins exactly when chunk N finishes
 * - Graceful cleanup: tracks active nodes so playback can be stopped and cleared instantly
 * - Malformed / empty chunk rejection: validates chunk integrity and byte boundaries
 * - AudioContext resource management: manages lifecycle and handles browser autoplay policies
 */

class AudioPlayer {
  /**
   * @param {Object} options
   * @param {number} [options.sampleRate=24000] Audio sample rate in Hz (default: 24000 for Gemini Live).
   * @param {number} [options.channels=1] Number of audio channels (default: 1 mono).
   * @param {function(): void} [options.onPlaybackStarted] Callback when playback begins.
   * @param {function(): void} [options.onPlaybackEnded] Callback when all scheduled audio finishes.
   */
  constructor(options = {}) {
    this.sampleRate = options.sampleRate || 24000;
    this.channels = options.channels || 1;
    this.audioContext = null;
    this.nextPlayTime = 0;
    this.activeSources = new Set();
    this.isPlaying = false;
    this.currentTurnId = 1;
    this.onPlaybackStarted = options.onPlaybackStarted || null;
    this.onPlaybackEnded = options.onPlaybackEnded || null;
    this._idleCheckTimer = null;
  }

  /**
   * Ensure the Web Audio API AudioContext is initialized and in the 'running' state.
   */
  async _ensureContext() {
    if (!this.audioContext || this.audioContext.state === "closed") {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      this.audioContext = new AudioContextClass({ sampleRate: this.sampleRate });
      this.nextPlayTime = 0;
    }

    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume();
    }
  }

  /**
   * Enqueue and play a raw 16-bit PCM audio chunk progressively.
   * 
   * @param {ArrayBuffer|Uint8Array|Int16Array} chunk Raw PCM 16-bit little-endian audio bytes.
   * @param {number} [turnId] Generation sequence ID to guard against race condition stale audio.
   */
  async playChunk(chunk, turnId = null) {
    if (!chunk) return;

    // Check against stale turns (barge-in guard)
    if (turnId !== null && turnId !== undefined) {
      if (turnId < this.currentTurnId) {
        return; // Drop stale audio chunk from interrupted turn
      }
      if (turnId > this.currentTurnId) {
        this.currentTurnId = turnId;
      }
    }

    // 1. Normalize input to ArrayBuffer
    let arrayBuffer;
    if (chunk instanceof ArrayBuffer) {
      arrayBuffer = chunk;
    } else if (ArrayBuffer.isView(chunk)) {
      arrayBuffer = chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength);
    } else {
      console.warn("[AudioPlayer] Discarding invalid chunk type:", typeof chunk);
      return;
    }

    // 2. Validate chunk size: 16-bit PCM requires an even number of bytes
    if (arrayBuffer.byteLength < 2) {
      return; // Ignore empty or single-byte malformed fragments
    }

    const validByteLength = arrayBuffer.byteLength - (arrayBuffer.byteLength % 2);
    if (validByteLength !== arrayBuffer.byteLength) {
      console.warn("[AudioPlayer] Truncating odd-byte audio chunk from", arrayBuffer.byteLength, "to", validByteLength);
      arrayBuffer = arrayBuffer.slice(0, validByteLength);
    }

    await this._ensureContext();

    // 3. Convert Int16 little-endian samples to Float32 [-1.0, 1.0]
    const sampleCount = validByteLength / 2;
    const view = new DataView(arrayBuffer);
    const float32Data = new Float32Array(sampleCount);

    for (let i = 0; i < sampleCount; i++) {
      const int16Sample = view.getInt16(i * 2, true); // little-endian
      float32Data[i] = int16Sample / 32768.0;
    }

    try {
      // 4. Create single-channel AudioBuffer
      const audioBuffer = this.audioContext.createBuffer(
        this.channels,
        sampleCount,
        this.sampleRate
      );
      audioBuffer.getChannelData(0).set(float32Data);

      // 5. Create and wire AudioBufferSourceNode
      const sourceNode = this.audioContext.createBufferSource();
      sourceNode.buffer = audioBuffer;
      sourceNode.connect(this.audioContext.destination);

      // 6. Calculate contiguous start time on AudioContext timeline
      const currentTime = this.audioContext.currentTime;
      let startTime = this.nextPlayTime;

      // If nextPlayTime is in the past (due to network jitter or pause), resync with small cushion
      if (startTime < currentTime) {
        startTime = currentTime + 0.015; // 15ms lead-in cushion prevents clicks
      }

      sourceNode.start(startTime);
      this.nextPlayTime = startTime + audioBuffer.duration;

      // 7. Track active sources for instant cancellation/clear
      this.activeSources.add(sourceNode);
      if (!this.isPlaying) {
        this.isPlaying = true;
        if (this.onPlaybackStarted) {
          this.onPlaybackStarted();
        }
      }

      sourceNode.onended = () => {
        this.activeSources.delete(sourceNode);
        try {
          sourceNode.disconnect();
        } catch (e) {
          // Disconnect safe
        }
        this._scheduleIdleCheck();
      };
    } catch (playErr) {
      console.warn("[AudioPlayer] Error playing audio chunk:", playErr);
    }
  }

  /**
   * Schedule check to verify if all active audio sources have finished playing.
   */
  _scheduleIdleCheck() {
    clearTimeout(this._idleCheckTimer);
    this._idleCheckTimer = setTimeout(() => {
      if (this.activeSources.size === 0) {
        this.isPlaying = false;
        if (this.onPlaybackEnded) {
          this.onPlaybackEnded();
        }
      }
    }, 50);
  }

  /**
   * Immediately stop and clear all pending and active audio chunks.
   * 
   * @param {number} [newTurnId] Optional next generation sequence ID.
   */
  stop(newTurnId = null) {
    clearTimeout(this._idleCheckTimer);

    if (newTurnId !== null && newTurnId !== undefined) {
      this.currentTurnId = Math.max(this.currentTurnId, newTurnId);
    } else {
      this.currentTurnId++;
    }

    // Stop and disconnect every active source node immediately
    for (const source of this.activeSources) {
      try {
        source.stop(0);
        source.disconnect();
      } catch (e) {
        // Node may have already ended
      }
    }
    this.activeSources.clear();

    if (this.audioContext && this.audioContext.state !== "closed") {
      this.nextPlayTime = this.audioContext.currentTime;
    } else {
      this.nextPlayTime = 0;
    }

    if (this.isPlaying) {
      this.isPlaying = false;
      if (this.onPlaybackEnded) {
        this.onPlaybackEnded();
      }
    }
  }

  /**
   * Clean up and close all Web Audio resources.
   */
  async close() {
    this.stop();
    if (this.audioContext && this.audioContext.state !== "closed") {
      try {
        await this.audioContext.close();
      } catch (e) {
        // Context might already be closed
      }
      this.audioContext = null;
    }
  }
}

// Attach to window object for global availability
if (typeof window !== "undefined") {
  window.AudioPlayer = AudioPlayer;
}
