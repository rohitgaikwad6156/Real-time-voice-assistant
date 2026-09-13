/**
 * AudioPlayer: resilient progressive PCM audio streaming player.
 *
 * Designed for Gemini Live API audio output (24,000 Hz, 16-bit linear PCM little-endian).
 * The player keeps a small playout cushion so normal WebSocket/network jitter does not
 * create gaps between tiny PCM chunks. If a true underrun happens, it briefly re-buffers
 * instead of repeatedly restarting audio every few milliseconds.
 */

class AudioPlayer {
  /**
   * @param {Object} options
   * @param {number} [options.sampleRate=24000] Source PCM sample rate.
   * @param {number} [options.channels=1] Number of output channels.
   * @param {number} [options.startupBufferSeconds=0.14] Initial playout cushion.
   * @param {number} [options.rebufferSeconds=0.09] Cushion used after a real underrun.
   * @param {number} [options.underrunThresholdSeconds=0.012] Minimum safe scheduled lead.
   * @param {number} [options.idleGraceMs=280] Grace period before declaring playback idle.
   * @param {function(): void} [options.onPlaybackStarted]
   * @param {function(): void} [options.onPlaybackEnded]
   */
  constructor(options = {}) {
    this.sampleRate = options.sampleRate || 24000;
    this.channels = options.channels || 1;

    this.startupBufferSeconds = Number.isFinite(options.startupBufferSeconds)
      ? options.startupBufferSeconds
      : 0.14;
    this.rebufferSeconds = Number.isFinite(options.rebufferSeconds)
      ? options.rebufferSeconds
      : 0.09;
    this.underrunThresholdSeconds = Number.isFinite(options.underrunThresholdSeconds)
      ? options.underrunThresholdSeconds
      : 0.012;
    this.idleGraceMs = Number.isFinite(options.idleGraceMs)
      ? options.idleGraceMs
      : 280;

    this.audioContext = null;
    this.nextPlayTime = 0;
    this.activeSources = new Set();
    this.isPlaying = false;

    // Cancellation entries expire automatically. This prevents a turn ID reused after
    // a reconnect from being muted forever.
    this.cancelledTurnIds = new Map();
    this.currentTurnId = null;

    this.onPlaybackStarted = options.onPlaybackStarted || null;
    this.onPlaybackEnded = options.onPlaybackEnded || null;
    this._idleCheckTimer = null;
    this._underrunCount = 0;
  }

  /** Ensure the Web Audio AudioContext exists and is running when possible. */
  _ensureContext() {
    if (!this.audioContext || this.audioContext.state === "closed") {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) {
        throw new Error("Web Audio API is not supported in this browser.");
      }

      // Prefer the browser/device native AudioContext rate. AudioBuffer itself is created
      // at the Gemini source rate (24 kHz), and Web Audio performs high-quality resampling.
      try {
        this.audioContext = new AudioContextClass({ latencyHint: "interactive" });
      } catch (e) {
        this.audioContext = new AudioContextClass();
      }
      this.nextPlayTime = 0;
    }

    if (this.audioContext.state === "suspended") {
      this.audioContext.resume().catch(() => {});
    }
  }

  _pruneCancelledTurns(now = Date.now()) {
    for (const [turnId, expiresAt] of this.cancelledTurnIds.entries()) {
      if (expiresAt <= now) this.cancelledTurnIds.delete(turnId);
    }
  }

  _isTurnCancelled(turnId) {
    if (turnId === null || turnId === undefined) return false;
    this._pruneCancelledTurns();
    return this.cancelledTurnIds.has(turnId);
  }

  _markTurnCancelled(turnId) {
    if (turnId === null || turnId === undefined) return;
    // Long enough to reject late packets from the interrupted response, short enough that
    // reconnects which restart turn numbering do not poison future playback.
    this.cancelledTurnIds.set(turnId, Date.now() + 15000);
  }

  /**
   * Enqueue a raw 16-bit PCM chunk for gap-resistant progressive playback.
   *
   * @param {ArrayBuffer|Uint8Array|Int16Array} chunk
   * @param {number|string|null} [turnId]
   */
  playChunk(chunk, turnId = null) {
    if (!chunk || this._isTurnCancelled(turnId)) return;

    let arrayBuffer;
    if (chunk instanceof ArrayBuffer) {
      arrayBuffer = chunk;
    } else if (ArrayBuffer.isView(chunk)) {
      arrayBuffer = chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength);
    } else {
      console.warn("[AudioPlayer] Discarding invalid chunk type:", typeof chunk);
      return;
    }

    if (arrayBuffer.byteLength < 2) return;

    const validByteLength = arrayBuffer.byteLength - (arrayBuffer.byteLength % 2);
    if (validByteLength !== arrayBuffer.byteLength) {
      console.warn(
        "[AudioPlayer] Truncating odd-byte PCM chunk from",
        arrayBuffer.byteLength,
        "to",
        validByteLength
      );
      arrayBuffer = arrayBuffer.slice(0, validByteLength);
    }

    this._ensureContext();

    const sampleCount = validByteLength / 2;
    if (sampleCount <= 0) return;

    const view = new DataView(arrayBuffer);
    const float32Data = new Float32Array(sampleCount);
    for (let i = 0; i < sampleCount; i++) {
      float32Data[i] = view.getInt16(i * 2, true) / 32768.0;
    }

    try {
      const audioBuffer = this.audioContext.createBuffer(
        this.channels,
        sampleCount,
        this.sampleRate
      );
      audioBuffer.getChannelData(0).set(float32Data);

      const sourceNode = this.audioContext.createBufferSource();
      sourceNode.buffer = audioBuffer;
      sourceNode.connect(this.audioContext.destination);

      const currentTime = this.audioContext.currentTime;
      const safeLeadBoundary = currentTime + this.underrunThresholdSeconds;
      let startTime;

      if (!this.isPlaying || this.nextPlayTime <= 0) {
        // Give the first few network packets time to arrive before sound starts.
        startTime = currentTime + this.startupBufferSeconds;
      } else if (this.nextPlayTime <= safeLeadBoundary) {
        // A true underrun (or near-underrun) occurred. Rebuild a small cushion once,
        // rather than restarting each newly-arrived chunk with a tiny 15 ms gap.
        startTime = currentTime + this.rebufferSeconds;
        this._underrunCount += 1;
        if (this._underrunCount <= 3 || this._underrunCount % 10 === 0) {
          console.debug("[AudioPlayer] Network jitter underrun; rebuffering", {
            count: this._underrunCount,
            rebufferMs: Math.round(this.rebufferSeconds * 1000),
          });
        }
      } else {
        // Normal path: schedule exactly after the previous PCM chunk with no overlap/gap.
        startTime = this.nextPlayTime;
      }

      sourceNode.start(startTime);
      this.nextPlayTime = startTime + audioBuffer.duration;
      this.activeSources.add(sourceNode);
      if (turnId !== null && turnId !== undefined) this.currentTurnId = turnId;

      // A newly scheduled source means playback is active even if it starts slightly in
      // the future. This also keeps microphone echo-gating stable across network jitter.
      clearTimeout(this._idleCheckTimer);
      this._idleCheckTimer = null;

      if (!this.isPlaying) {
        this.isPlaying = true;
        if (this.onPlaybackStarted) this.onPlaybackStarted();
      }

      sourceNode.onended = () => {
        this.activeSources.delete(sourceNode);
        try {
          sourceNode.disconnect();
        } catch (_) {}
        this._scheduleIdleCheck();
      };
    } catch (playErr) {
      console.warn("[AudioPlayer] Error playing audio chunk:", playErr);
    }
  }

  /**
   * Wait through a short network gap before declaring playback finished. This prevents
   * the app from briefly reopening the microphone between late output packets.
   */
  _scheduleIdleCheck() {
    clearTimeout(this._idleCheckTimer);
    this._idleCheckTimer = setTimeout(() => {
      this._idleCheckTimer = null;
      if (this.activeSources.size !== 0) return;

      // If the scheduling cursor is still in the future, a source may be about to start.
      if (this.audioContext && this.nextPlayTime > this.audioContext.currentTime + 0.005) {
        this._scheduleIdleCheck();
        return;
      }

      this.isPlaying = false;
      this.currentTurnId = null;
      this.nextPlayTime = this.audioContext && this.audioContext.state !== "closed"
        ? this.audioContext.currentTime
        : 0;
      if (this.onPlaybackEnded) this.onPlaybackEnded();
    }, this.idleGraceMs);
  }

  /**
   * Immediately stop all pending/active audio.
   *
   * If the supplied turnId is the currently-playing turn it is cancelled. If the server
   * sends the NEXT turn id in an interruption event, it is deliberately not cancelled.
   * With no turnId, the current active turn is cancelled automatically.
   *
   * @param {number|string|null} [turnId]
   */
  stop(turnId = null) {
    clearTimeout(this._idleCheckTimer);
    this._idleCheckTimer = null;

    const turnToCancel = (turnId === null || turnId === undefined)
      ? this.currentTurnId
      : (turnId === this.currentTurnId ? turnId : null);
    this._markTurnCancelled(turnToCancel);

    for (const source of this.activeSources) {
      try {
        source.onended = null;
        source.stop(0);
        source.disconnect();
      } catch (_) {
        // Source may have already completed.
      }
    }
    this.activeSources.clear();

    if (this.audioContext && this.audioContext.state !== "closed") {
      this.nextPlayTime = this.audioContext.currentTime;
    } else {
      this.nextPlayTime = 0;
    }

    this.currentTurnId = null;
    if (this.isPlaying) {
      this.isPlaying = false;
      if (this.onPlaybackEnded) this.onPlaybackEnded();
    }
  }

  /** Clean up and close Web Audio resources. */
  async close() {
    this.stop();
    if (this.audioContext && this.audioContext.state !== "closed") {
      try {
        await this.audioContext.close();
      } catch (_) {}
      this.audioContext = null;
    }
    this.nextPlayTime = 0;
    this.cancelledTurnIds.clear();
  }
}

if (typeof window !== "undefined") {
  window.AudioPlayer = AudioPlayer;
}
