/**
 * Reusable AudioStreamer module.
 * 
 * Captures live microphone audio using browser Web Audio APIs,
 * performs sample rate conversion (downsampling to 16,000 Hz),
 * converts Float32 audio samples into 16-bit linear PCM (little-endian),
 * and streams the resulting PCM ArrayBuffers continuously.
 */

class AudioStreamer {
  /**
   * @param {Object} options
   * @param {number} [options.targetSampleRate=16000] Target sample rate in Hz (Gemini Live API requires 16000).
   * @param {number} [options.bufferSize=2048] Audio buffer size for ScriptProcessorNode.
   */
  constructor(options = {}) {
    this.targetSampleRate = options.targetSampleRate || 16000;
    this.bufferSize = options.bufferSize || 2048;
    this.mediaStream = null;
    this.audioContext = null;
    this.sourceNode = null;
    this.processorNode = null;
    this.muteNode = null;
    this.isRecording = false;
    this.onVoiceActivity = options.onVoiceActivity || null;
  }

  /**
   * Request microphone permission and start continuous audio streaming.
   * 
   * @param {function(ArrayBuffer): void} onAudioChunk Callback receiving raw Int16 PCM chunks.
   * @returns {Promise<void>}
   */
  async start(onAudioChunk) {
    if (this.isRecording) {
      return;
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("Your browser does not support microphone capture via navigator.mediaDevices.");
    }

    // 1. Request microphone access with voice constraints
    try {
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: this.targetSampleRate,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      if (err.name === "NotAllowedError" || err.name === "PermissionDeniedError") {
        throw new Error("Microphone permission was denied. Please allow microphone access in your browser.");
      } else if (err.name === "NotFoundError" || err.name === "DevicesNotFoundError") {
        throw new Error("No microphone device was found on this system.");
      } else if (err.name === "NotReadableError") {
        throw new Error("Microphone is currently in use by another application or process.");
      } else {
        throw new Error(`Microphone access failed: ${err.message || err.name}`);
      }
    }

    // 2. Initialize AudioContext & processing graph
    let inputSampleRate;
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) {
        throw new Error("Web Audio API is not supported in this browser.");
      }
      this.audioContext = new AudioContextClass({ sampleRate: this.targetSampleRate });
      if (this.audioContext.state === "suspended") {
        await this.audioContext.resume();
      }

      inputSampleRate = this.audioContext.sampleRate;

      // 3. Create audio processing graph
      this.sourceNode = this.audioContext.createMediaStreamSource(this.mediaStream);
      this.processorNode = this.audioContext.createScriptProcessor(this.bufferSize, 1, 1);

      // Mute gain node to prevent speaker acoustic feedback
      this.muteNode = this.audioContext.createGain();
      this.muteNode.gain.value = 0;
    } catch (audioInitErr) {
      this.stop();
      throw new Error(`Audio initialization failure: ${audioInitErr.message || audioInitErr.name}`);
    }

    this.processorNode.onaudioprocess = (event) => {
      if (!this.isRecording) return;
      const inputChannelData = event.inputBuffer.getChannelData(0);

      // Calculate audio power (RMS) for real-time barge-in speech detection
      if (this.onVoiceActivity) {
        let sum = 0;
        for (let i = 0; i < inputChannelData.length; i++) {
          sum += inputChannelData[i] * inputChannelData[i];
        }
        const rms = Math.sqrt(sum / inputChannelData.length);
        this.onVoiceActivity(rms);
      }

      // Resample to target sample rate (16 kHz) if the hardware AudioContext runs at 44.1k/48k
      const resampled = this._resample(inputChannelData, inputSampleRate, this.targetSampleRate);

      // Convert Float32Array to 16-bit linear PCM (little-endian)
      const pcmBuffer = this._floatTo16BitPCM(resampled);

      if (onAudioChunk && pcmBuffer.byteLength > 0) {
        onAudioChunk(pcmBuffer);
      }
    };

    // Connect nodes: Source -> Processor -> Mute -> Destination
    this.sourceNode.connect(this.processorNode);
    this.processorNode.connect(this.muteNode);
    this.muteNode.connect(this.audioContext.destination);

    this.isRecording = true;
  }

  /**
   * Stop audio capture, disconnect all audio nodes, and release media stream tracks.
   */
  stop() {
    this.isRecording = false;

    if (this.processorNode) {
      this.processorNode.onaudioprocess = null;
      this.processorNode.disconnect();
      this.processorNode = null;
    }

    if (this.sourceNode) {
      this.sourceNode.disconnect();
      this.sourceNode = null;
    }

    if (this.muteNode) {
      this.muteNode.disconnect();
      this.muteNode = null;
    }

    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => {
        try {
          track.stop();
        } catch (e) {
          // Ignore track stop errors
        }
      });
      this.mediaStream = null;
    }

    if (this.audioContext && this.audioContext.state !== "closed") {
      this.audioContext.close().catch(() => {});
      this.audioContext = null;
    }
  }

  /**
   * Downsample audio buffer using linear accumulation.
   * 
   * @param {Float32Array} buffer Input audio samples.
   * @param {number} fromRate Source sample rate.
   * @param {number} toRate Target sample rate.
   * @returns {Float32Array} Resampled audio samples.
   */
  _resample(buffer, fromRate, toRate) {
    if (fromRate === toRate) {
      return buffer;
    }
    const ratio = fromRate / toRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;

    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
      let accum = 0;
      let count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
        accum += buffer[i];
        count++;
      }
      result[offsetResult] = count > 0 ? accum / count : 0;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  }

  /**
   * Convert normalized Float32 values [-1.0, 1.0] to signed 16-bit PCM ArrayBuffer (little-endian).
   * 
   * @param {Float32Array} float32Array
   * @returns {ArrayBuffer}
   */
  _floatTo16BitPCM(float32Array) {
    const buffer = new ArrayBuffer(float32Array.length * 2);
    const view = new DataView(buffer);
    for (let i = 0; i < float32Array.length; i++) {
      let s = Math.max(-1, Math.min(1, float32Array[i]));
      // Map [-1.0, 1.0] to [-32768, 32767], little-endian (true)
      view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    }
    return buffer;
  }
}

// Attach to window object for global availability
if (typeof window !== "undefined") {
  window.AudioStreamer = AudioStreamer;
}
