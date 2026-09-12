/**
 * Real-Time Voice Assistant Client Application
 *
 * Canonical Vercel frontend runtime.
 * Authentication and conversation selection are resolved by auth.js before this
 * file is loaded. app.js never creates an anonymous voice WebSocket.
 */

// DOM elements are bound during one-time initialization so this file is safe
// whether it loads before or after DOMContentLoaded.
let recordButton = null;
let recordButtonText = null;
let textInput = null;
let textButton = null;
let textForm = null;
let voiceOrb = null;
let waveformCanvas = null;
let heroPrompt = null;
let connectionStatus = null;
let connectionText = null;
let stateBadge = null;
let recordStatus = null;
let toolActivityBanner = null;
let toolIcon = null;
let toolActivityText = null;
let conversationList = null;
let emptyHint = null;
let turnCounter = null;
let clearConversationBtn = null;
let toastContainer = null;
let statusItemConnected = null;
let statusItemListening = null;
let statusItemThinking = null;
let statusItemSpeaking = null;

function bindDomElements() {
  recordButton = document.getElementById("recordButton");
  recordButtonText = document.getElementById("recordButtonText");
  textInput = document.getElementById("textInput");
  textButton = document.getElementById("textButton");
  textForm = document.getElementById("textForm");
  voiceOrb = document.getElementById("voiceOrb");
  waveformCanvas = document.getElementById("waveformCanvas");
  heroPrompt = document.getElementById("heroPrompt");
  connectionStatus = document.getElementById("connectionStatus");
  connectionText = document.getElementById("connectionText");
  stateBadge = document.getElementById("stateBadge");
  recordStatus = document.getElementById("recordStatus");
  toolActivityBanner = document.getElementById("toolActivityBanner");
  toolIcon = document.getElementById("toolIcon");
  toolActivityText = document.getElementById("toolActivityText");
  conversationList = document.getElementById("conversationList");
  emptyHint = document.getElementById("emptyHint");
  turnCounter = document.getElementById("turnCounter");
  clearConversationBtn = document.getElementById("clearConversationBtn");
  toastContainer = document.getElementById("toastContainer");
  statusItemConnected = document.getElementById("statusItemConnected");
  statusItemListening = document.getElementById("statusItemListening");
  statusItemThinking = document.getElementById("statusItemThinking");
  statusItemSpeaking = document.getElementById("statusItemSpeaking");
}

function getAppConfig() {
  const config = window.APP_CONFIG;
  if (!config?.API_URL || !config?.WS_URL || !config?.AUTH_TOKEN || !config?.CONVERSATION_ID) return null;
  return config;
}

// State & lifecycle.
let appInitialized = false;
let eventListenersBound = false;
let websocket = null;
let reconnectTimer = null;
let audioStreamer = null;
let audioPlayer = null;
let isStreaming = false;
let chunksSent = 0;
let totalTurns = 0;
let currentAssistantState = "idle";
let lastBargeInTimestamp = 0;
let currentVoiceEnergy = 0.0;
let toolActivityTimer = null;
let conversationSwitchInProgress = false;
const BARGE_IN_COOLDOWN_MS = 200;

// Conversation turn state.
let currentUserBubble = null;
let currentAssistantBubble = null;
let currentUserTranscript = "";
let finalUserTranscript = "";
let isUserTurnActive = false;
let isSendingText = false;
let pendingSentText = null;

function setControlsEnabled(enabled) {
  if (recordButton) recordButton.disabled = !enabled;
  if (textInput) textInput.disabled = !enabled;
  if (textButton) textButton.disabled = !enabled;
}

function setAssistantState(state, customText = null) {
  currentAssistantState = state;

  if (voiceOrb) voiceOrb.className = `voice-orb state-${state}`;
  if (stateBadge) {
    stateBadge.className = `state-badge state-${state}`;
    stateBadge.textContent = state.toUpperCase();
  }

  if (recordButton) {
    if (state === "listening" || isStreaming) {
      recordButton.classList.add("streaming");
      if (recordButtonText) recordButtonText.textContent = "Stop Speaking";
    } else {
      recordButton.classList.remove("streaming");
      if (recordButtonText) recordButtonText.textContent = "Start Speaking";
    }
  }

  if (statusItemListening) statusItemListening.classList.toggle("active", state === "listening");
  if (statusItemThinking) statusItemThinking.classList.toggle("active", state === "thinking");
  if (statusItemSpeaking) statusItemSpeaking.classList.toggle("active", state === "speaking");

  const promptMap = {
    idle: '"How can I help you today?"',
    connecting: '"Connecting to assistant..."',
    listening: '"Listening to your voice..."',
    thinking: '"Processing..."',
    speaking: '"Speaking..."',
    interrupted: '"Interrupted — listening..."',
    error: '"Encountered an issue"',
  };

  if (heroPrompt) {
    heroPrompt.textContent = customText ? `"${customText}"` : (promptMap[state] || promptMap.idle);
  }
  if (recordStatus) recordStatus.textContent = customText || state;
}

function setConnectionState(status, text) {
  if (connectionStatus) connectionStatus.className = `connection-pill ${status}`;
  if (connectionText) connectionText.textContent = text;
  if (statusItemConnected) statusItemConnected.classList.toggle("active", status === "connected");
}

// ------------------------------------------------------------------------------
// Waveform
// ------------------------------------------------------------------------------
let canvasCtx = null;
let animationFrameId = null;
let wavePhase = 0;

function initWaveform() {
  if (!waveformCanvas || animationFrameId !== null) return;
  canvasCtx = waveformCanvas.getContext("2d");
  if (!canvasCtx) return;

  function drawWave() {
    animationFrameId = requestAnimationFrame(drawWave);
    const width = waveformCanvas.width;
    const height = waveformCanvas.height;
    const centerY = height / 2;

    canvasCtx.clearRect(0, 0, width, height);

    let targetAmp = 2;
    let waveColor = "rgba(56, 189, 248, 0.4)";

    if (currentAssistantState === "listening") {
      targetAmp = Math.max(8, currentVoiceEnergy * 110);
      waveColor = "rgba(56, 189, 248, 0.85)";
    } else if (currentAssistantState === "speaking") {
      targetAmp = 14 + Math.sin(wavePhase * 2.5) * 6;
      waveColor = "rgba(16, 185, 129, 0.85)";
    } else if (currentAssistantState === "thinking") {
      targetAmp = 7 + Math.sin(wavePhase * 3) * 3;
      waveColor = "rgba(168, 85, 247, 0.75)";
    } else if (currentAssistantState === "interrupted") {
      targetAmp = 12;
      waveColor = "rgba(244, 63, 94, 0.85)";
    }

    wavePhase += 0.06;

    for (let layer = 0; layer < 2; layer++) {
      canvasCtx.beginPath();
      canvasCtx.lineWidth = layer === 0 ? 2.5 : 1.5;
      canvasCtx.strokeStyle = layer === 0 ? waveColor : waveColor.replace("0.85", "0.35");
      const freqMultiplier = layer === 0 ? 0.025 : 0.04;
      const speedMultiplier = layer === 0 ? 1 : 1.4;
      const amp = layer === 0 ? targetAmp : targetAmp * 0.6;

      for (let x = 0; x < width; x++) {
        const envelope = Math.sin((x / width) * Math.PI);
        const y = centerY + Math.sin(x * freqMultiplier + wavePhase * speedMultiplier) * amp * envelope;
        if (x === 0) canvasCtx.moveTo(x, y);
        else canvasCtx.lineTo(x, y);
      }
      canvasCtx.stroke();
    }
  }

  drawWave();
}

// ------------------------------------------------------------------------------
// Tool activity + toast
// ------------------------------------------------------------------------------
function showToolActivity(icon, text, autoHideMs = 3500) {
  if (!toolActivityBanner) return;
  clearTimeout(toolActivityTimer);
  if (toolIcon) toolIcon.textContent = icon;
  if (toolActivityText) toolActivityText.textContent = text;
  toolActivityBanner.hidden = false;

  if (autoHideMs > 0) {
    toolActivityTimer = setTimeout(() => {
      toolActivityBanner.hidden = true;
    }, autoHideMs);
  }
}

function hideToolActivity() {
  if (toolActivityBanner) toolActivityBanner.hidden = true;
}

let lastToastMessage = "";
let lastToastTime = 0;

function showToast(message, isError = true) {
  if (!toastContainer) return;
  const now = Date.now();
  if (message === lastToastMessage && now - lastToastTime < 2500) return;
  lastToastMessage = message;
  lastToastTime = now;

  while (toastContainer.children.length >= 3) {
    toastContainer.removeChild(toastContainer.firstElementChild);
  }

  const toast = document.createElement("div");
  toast.className = "toast";
  toast.innerHTML = `<span>${isError ? "⚠️" : "ℹ️"}</span><span>${message}</span>`;
  toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(20px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

// ------------------------------------------------------------------------------
// Audio
// ------------------------------------------------------------------------------
function base64ToArrayBuffer(base64) {
  const binaryString = window.atob(base64);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
  return bytes.buffer;
}

function getOrCreateAudioPlayer() {
  if (audioPlayer) return audioPlayer;
  if (typeof window.AudioPlayer !== "function") {
    console.warn("[AudioPlayer] AudioPlayer dependency is not available yet.");
    return null;
  }

  audioPlayer = new window.AudioPlayer({
    sampleRate: 24000,
    onPlaybackStarted: () => setAssistantState("speaking"),
    onPlaybackEnded: () => {
      if (currentAssistantState === "speaking") {
        setAssistantState(isStreaming ? "listening" : "idle");
      }
    },
  });
  return audioPlayer;
}

function getOrCreateAudioStreamer() {
  if (audioStreamer) return audioStreamer;
  if (typeof window.AudioStreamer !== "function") {
    console.warn("[AudioStreamer] AudioStreamer dependency is not available yet.");
    return null;
  }

  audioStreamer = new window.AudioStreamer({
    targetSampleRate: 16000,
    bufferSize: 2048,
    onVoiceActivity: (rms) => {
      currentVoiceEnergy = rms;
    },
  });
  return audioStreamer;
}

function initializeAudioSubsystem() {
  getOrCreateAudioPlayer();
  getOrCreateAudioStreamer();
}

async function ensureAudioPlayerReady() {
  const player = getOrCreateAudioPlayer();
  if (!player) throw new Error("Audio player is still loading. Please try again.");
  try {
    await player._ensureContext();
  } catch (err) {
    console.warn("[AudioPlayer] Context resume on gesture failed:", err);
  }
}

// ------------------------------------------------------------------------------
// Conversation UI
// ------------------------------------------------------------------------------
function updateTurnCounter() {
  if (turnCounter) {
    turnCounter.textContent = `${totalTurns} ${totalTurns === 1 ? "turn" : "turns"}`;
  }
}

function syncTurnCountFromDom() {
  if (!conversationList) return;
  totalTurns = conversationList.querySelectorAll(".user-turn").length;
  updateTurnCounter();
}

function refreshEmptyHintReference() {
  emptyHint = document.getElementById("emptyHint");
  return emptyHint;
}

function getOrCreateUserBubble() {
  const hint = refreshEmptyHintReference();
  if (hint) hint.style.display = "none";

  if (!currentUserBubble) {
    totalTurns++;
    updateTurnCounter();
    const turnDiv = document.createElement("div");
    turnDiv.className = "turn user-turn";
    turnDiv.innerHTML = `
      <div class="turn-header-row"><span class="turn-role-tag">USER</span></div>
      <div class="turn-bubble turn-interim">...</div>`;
    conversationList.appendChild(turnDiv);
    currentUserBubble = turnDiv.querySelector(".turn-bubble");
  }
  return currentUserBubble;
}

function getOrCreateAssistantBubble() {
  const hint = refreshEmptyHintReference();
  if (hint) hint.style.display = "none";

  if (!currentAssistantBubble) {
    const turnDiv = document.createElement("div");
    turnDiv.className = "turn assistant-turn";
    turnDiv.innerHTML = `
      <div class="turn-header-row"><span class="turn-role-tag">ASSISTANT</span></div>
      <div class="turn-bubble"></div>`;
    conversationList.appendChild(turnDiv);
    currentAssistantBubble = turnDiv.querySelector(".turn-bubble");
  }
  return currentAssistantBubble;
}

function scrollConversationToBottom() {
  if (conversationList) conversationList.scrollTop = conversationList.scrollHeight;
}

function updateUserTranscriptUI() {
  const displayText = currentUserTranscript.trimStart();
  if (textInput) textInput.value = displayText;
  const compatTranscript = document.getElementById("transcript");
  if (compatTranscript) compatTranscript.textContent = displayText;
  const bubble = getOrCreateUserBubble();
  bubble.textContent = displayText || "...";
  bubble.classList.add("turn-interim");
  scrollConversationToBottom();
}

function appendOrUpdateUserTranscript(incomingText) {
  if (!incomingText) return;
  if (!isUserTurnActive) {
    isUserTurnActive = true;
    currentUserTranscript = "";
    finalUserTranscript = "";
    currentUserBubble = null;
    currentAssistantBubble = null;
  }

  const cur = currentUserTranscript;
  const inc = incomingText;
  const curTrim = cur.trim();
  const incTrim = inc.trim();

  if (curTrim.length > 0 && (inc.startsWith(cur) || (incTrim.length > curTrim.length && incTrim.startsWith(curTrim)))) {
    currentUserTranscript = inc;
  } else {
    currentUserTranscript += inc;
  }
  updateUserTranscriptUI();
}

function finalizeUserTurn() {
  if (!isUserTurnActive && !currentUserBubble) return;

  if (currentUserTranscript.trim()) {
    finalUserTranscript = currentUserTranscript.trim();
    if (currentUserBubble) {
      currentUserBubble.textContent = finalUserTranscript;
      currentUserBubble.classList.remove("turn-interim");
    }
    if (textInput) textInput.value = finalUserTranscript;
    const compatTranscript = document.getElementById("transcript");
    if (compatTranscript) compatTranscript.textContent = finalUserTranscript;
  }

  currentUserBubble = null;
  isUserTurnActive = false;
}

function resetTransientConversationState() {
  currentUserBubble = null;
  currentAssistantBubble = null;
  currentUserTranscript = "";
  finalUserTranscript = "";
  isUserTurnActive = false;
  pendingSentText = null;
  isSendingText = false;
  chunksSent = 0;
  currentVoiceEnergy = 0.0;
  if (textInput) textInput.value = "";
  const compatTranscript = document.getElementById("transcript");
  if (compatTranscript) compatTranscript.textContent = "";
  hideToolActivity();
}

function handleClearConversation() {
  if (conversationList) {
    conversationList.innerHTML = `
      <div class="empty-hint" id="emptyHint">
        <div class="empty-icon">🎙️</div>
        <p class="empty-title">Ready to assist you</p>
        <p class="empty-desc">Click <strong>Start Speaking</strong> below or ask by text to begin your real-time conversation.</p>
      </div>`;
    refreshEmptyHintReference();
  }
  resetTransientConversationState();
  totalTurns = 0;
  updateTurnCounter();
}

// ------------------------------------------------------------------------------
// Barge-in
// ------------------------------------------------------------------------------
function handleBargeIn(source = "local") {
  const now = Date.now();
  if (now - lastBargeInTimestamp < BARGE_IN_COOLDOWN_MS) return;
  lastBargeInTimestamp = now;

  if (audioPlayer) audioPlayer.stop();

  if (currentAssistantBubble) {
    if (!currentAssistantBubble.querySelector(".interrupted-tag")) {
      const tag = document.createElement("span");
      tag.className = "interrupted-tag";
      tag.textContent = "⏹ Interrupted";
      currentAssistantBubble.appendChild(tag);
      const parentTurn = currentAssistantBubble.closest(".turn");
      if (parentTurn) parentTurn.classList.add("turn-interrupted");
    }
    currentAssistantBubble = null;
  }

  setAssistantState("interrupted");

  if (websocket && websocket.readyState === WebSocket.OPEN && source !== "server") {
    try {
      websocket.send(JSON.stringify({ type: "interrupt" }));
    } catch (err) {
      console.warn("[Barge-In] Send interrupt failed:", err);
    }
  }

  setTimeout(() => {
    if (currentAssistantState === "interrupted" && !conversationSwitchInProgress) {
      setAssistantState(isStreaming ? "listening" : "idle");
    }
  }, 450);
}

// ------------------------------------------------------------------------------
// WebSocket lifecycle
// ------------------------------------------------------------------------------
function clearReconnectTimer() {
  if (reconnectTimer !== null) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function scheduleReconnect(delayMs = 2500) {
  if (conversationSwitchInProgress || reconnectTimer !== null) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    initWebSocket();
  }, delayMs);
}

function initWebSocket() {
  if (conversationSwitchInProgress) return;

  const config = getAppConfig();
  if (!config) {
    console.warn("[WebSocket] Authenticated APP_CONFIG is missing; refusing anonymous connection.");
    setConnectionState("disconnected", "Sign in required");
    setAssistantState("idle", "Sign in to start assistant");
    setControlsEnabled(false);
    return;
  }

  if (websocket) {
    const state = websocket.readyState;
    if (state === WebSocket.OPEN || state === WebSocket.CONNECTING || state === WebSocket.CLOSING) return;
    websocket = null;
  }

  clearReconnectTimer();
  const wsUrl = config.WS_URL;
  console.log("[WebSocket] Connecting to authenticated conversation.");
  setConnectionState("connecting", "Connecting...");
  setAssistantState("connecting");

  let socket;
  try {
    socket = new WebSocket(wsUrl);
    websocket = socket;
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
      if (websocket !== socket || conversationSwitchInProgress) return;
      clearReconnectTimer();
      setConnectionState("connecting", "Authenticating...");
      setControlsEnabled(false);
      socket.send(JSON.stringify({
        type: "auth",
        token: config.AUTH_TOKEN,
        conversation_id: config.CONVERSATION_ID,
      }));
    };

    socket.onmessage = (event) => {
      if (websocket !== socket || conversationSwitchInProgress) return;
      try {
        handleServerMessage(JSON.parse(event.data));
      } catch (err) {
        console.warn("[WebSocket] Non-JSON message:", event.data);
      }
    };

    socket.onerror = (err) => {
      if (websocket !== socket || conversationSwitchInProgress) return;
      console.error("[WebSocket] Error:", err);
      showToast("Connection encountered an error.");
      setConnectionState("disconnected", "Error");
    };

    socket.onclose = (event) => {
      if (websocket !== socket) return;
      websocket = null;

      if (conversationSwitchInProgress) return;

      console.warn("[WebSocket] Disconnected code:", event.code);
      if (isStreaming) stopStreaming();
      setConnectionState("disconnected", "Disconnected");
      setAssistantState("idle");
      scheduleReconnect(2500);
    };
  } catch (err) {
    if (websocket === socket) websocket = null;
    if (conversationSwitchInProgress) return;
    console.error("[WebSocket] Connection attempt failed:", err);
    showToast("Could not connect to voice assistant server.");
    setConnectionState("disconnected", "Offline");
    scheduleReconnect(3000);
  }
}

function beginConversationSwitch() {
  conversationSwitchInProgress = true;
  clearReconnectTimer();
  setControlsEnabled(false);

  if (audioStreamer) {
    try { audioStreamer.stop(); } catch (_) {}
  }
  isStreaming = false;

  if (audioPlayer) {
    try { audioPlayer.stop(); } catch (_) {}
  }

  resetTransientConversationState();

  // Detach the current socket before closing it. Its callbacks all check object
  // identity, so late packets from the old conversation are ignored.
  const oldSocket = websocket;
  websocket = null;
  if (oldSocket && oldSocket.readyState !== WebSocket.CLOSED) {
    try { oldSocket.close(1000, "conversation-switch"); } catch (_) {}
  }

  setConnectionState("connecting", "Switching conversation...");
  setAssistantState("connecting", "Switching conversation...");
}

function completeConversationSwitch(options = {}) {
  const turnCount = Number.isFinite(options.turnCount) ? options.turnCount : null;
  if (turnCount !== null) {
    totalTurns = turnCount;
    updateTurnCounter();
  } else {
    syncTurnCountFromDom();
  }

  refreshEmptyHintReference();
  resetTransientConversationState();
  conversationSwitchInProgress = false;

  const config = getAppConfig();
  if (!config) {
    setConnectionState("disconnected", "Sign in required");
    setAssistantState("idle", "Sign in to start assistant");
    setControlsEnabled(false);
    return;
  }

  setConnectionState("connecting", "Connecting...");
  setAssistantState("connecting");
  initWebSocket();
}

function cancelConversationSwitch(options = {}) {
  conversationSwitchInProgress = false;
  if (Number.isFinite(options.turnCount)) {
    totalTurns = options.turnCount;
    updateTurnCounter();
  } else {
    syncTurnCountFromDom();
  }
  refreshEmptyHintReference();
  initWebSocket();
}

// Public bridge used by auth.js for in-place sidebar switching.
window.VOICE_APP_BEGIN_CONVERSATION_SWITCH = beginConversationSwitch;
window.VOICE_APP_COMPLETE_CONVERSATION_SWITCH = completeConversationSwitch;
window.VOICE_APP_CANCEL_CONVERSATION_SWITCH = cancelConversationSwitch;
window.VOICE_APP_SYNC_TURN_COUNT = (count) => {
  if (Number.isFinite(count)) {
    totalTurns = count;
    updateTurnCounter();
  } else {
    syncTurnCountFromDom();
  }
};

function handleServerMessage(message) {
  if (conversationSwitchInProgress) return;

  if (message.type === "status") {
    if (message.status === "authenticated" || message.status === "ready") {
      setConnectionState("connected", "Connected");
      setAssistantState(isStreaming ? "listening" : "idle");
      setControlsEnabled(true);
    } else if (message.status === "connected") {
      setConnectionState("connecting", "Authenticating...");
      setControlsEnabled(false);
    } else if (message.status === "streaming") {
      setAssistantState("listening", `Listening (${chunksSent} chunks sent)`);
    } else if (message.status === "stopped") {
      setAssistantState("idle", "Streaming paused");
    } else if (message.status === "error") {
      showToast(message.message || "Server error occurred.");
      setAssistantState("error", message.message);
      if (isStreaming) stopStreaming();
    } else if (message.status === "auth_error" || message.status === "auth_required") {
      const authMessage = message.message || "Authentication is required.";
      showToast(authMessage);
      setControlsEnabled(false);
      setConnectionState("disconnected", "Sign in required");
      if (isStreaming) stopStreaming();
      if (websocket) websocket.close(1000, "Authentication required");
      if (typeof window.VOICE_HANDLE_AUTH_FAILURE === "function") {
        window.VOICE_HANDLE_AUTH_FAILURE(authMessage);
      }
    }
  } else if (message.type === "interrupted") {
    if (isStreaming && currentVoiceEnergy > 0.03) {
      if (audioPlayer) audioPlayer.stop(message.turn_id);
      handleBargeIn("server");
    }
  } else if (message.type === "transcript") {
    const role = (message.role || "user").toLowerCase();
    const text = message.text || "";

    if (role === "user") {
      const trimmedText = text.trim();
      if (pendingSentText && (trimmedText === pendingSentText.trim() || trimmedText === pendingSentText)) {
        pendingSentText = null;
        if (message.is_final) setAssistantState("thinking");
        return;
      }

      setAssistantState("listening");
      appendOrUpdateUserTranscript(text);
      if (message.is_final) {
        if (currentUserBubble) currentUserBubble.classList.remove("turn-interim");
        setAssistantState("thinking");
      }
    } else {
      finalizeUserTurn();
      const bubble = getOrCreateAssistantBubble();
      bubble.textContent += text;
    }
    scrollConversationToBottom();
  } else if (message.type === "text") {
    finalizeUserTurn();
    const bubble = getOrCreateAssistantBubble();
    bubble.textContent += message.text || "";
    scrollConversationToBottom();
  } else if (message.type === "audio") {
    finalizeUserTurn();
    if (message.data) {
      const player = getOrCreateAudioPlayer();
      if (!player) return;
      try {
        player.playChunk(base64ToArrayBuffer(message.data), message.turn_id);
      } catch (err) {
        console.error("[AudioPlayer] Playback error:", err);
      }
    }
  } else if (message.type === "tool_call") {
    finalizeUserTurn();
    setAssistantState("thinking");
    const calls = (message.function_calls || []).map((call) => call.name);
    let toolLabel = calls.join(", ");
    let icon = "🔧";

    if (calls.includes("get_weather")) {
      toolLabel = "Checking weather...";
      icon = "🌤️";
    } else if (calls.includes("create_reminder")) {
      toolLabel = "Creating reminder...";
      icon = "⏰";
    } else if (calls.includes("search_notes")) {
      toolLabel = "Searching notes...";
      icon = "📝";
    }

    showToolActivity(icon, toolLabel, 0);
    const bubble = getOrCreateAssistantBubble();
    bubble.innerHTML += ` <span class="tool-chip">${icon} ${toolLabel}</span>`;
    scrollConversationToBottom();
  } else if (message.type === "tool_result") {
    const toolName = message.name || "Tool";
    const res = message.result || {};
    let summaryText = `✓ ${toolName} completed`;
    let icon = "✓";

    if (toolName === "get_weather") {
      summaryText = `✓ Weather received: ${res.city || "City"} (${res.temperature}°${res.unit === "fahrenheit" ? "F" : "C"})`;
      icon = "🌤️";
    } else if (toolName === "create_reminder") {
      summaryText = `✓ Reminder created: "${res.reminder?.title || "Task"}"`;
      icon = "⏰";
    } else if (toolName === "search_notes") {
      summaryText = `✓ Found ${res.count || 0} note(s) matching "${res.query || ""}"`;
      icon = "📝";
    }
    showToolActivity(icon, summaryText, 3500);
  } else if (message.type === "turn_complete") {
    finalizeUserTurn();
    currentAssistantBubble = null;
    if (currentAssistantState !== "speaking" && currentAssistantState !== "interrupted") {
      setAssistantState(isStreaming ? "listening" : "idle");
    }
  }
}

// ------------------------------------------------------------------------------
// Microphone streaming
// ------------------------------------------------------------------------------
async function startStreaming() {
  if (conversationSwitchInProgress) {
    showToast("Conversation is switching. Please wait a moment...", false);
    return;
  }

  if (!websocket || websocket.readyState !== WebSocket.OPEN) {
    showToast("Connecting to server. Please wait a moment...");
    initWebSocket();
    return;
  }

  try {
    await ensureAudioPlayerReady();
  } catch (err) {
    showToast(err.message || "Audio is not ready yet.");
    return;
  }

  const streamer = getOrCreateAudioStreamer();
  if (!streamer) {
    showToast("Microphone support is still loading. Please try again.");
    return;
  }

  if (audioPlayer) audioPlayer.stop();
  finalizeUserTurn();
  currentUserTranscript = "";
  finalUserTranscript = "";
  isUserTurnActive = false;
  currentUserBubble = null;
  currentAssistantBubble = null;
  if (textInput) textInput.value = "";
  chunksSent = 0;

  try {
    websocket.send(JSON.stringify({ type: "start_audio" }));
    await streamer.start((pcmChunk) => {
      if (conversationSwitchInProgress) return;
      if (websocket && websocket.readyState === WebSocket.OPEN) {
        if (audioPlayer && audioPlayer.isPlaying) return;
        websocket.send(pcmChunk);
        chunksSent++;
        if (chunksSent % 20 === 0 && currentAssistantState === "listening") {
          setAssistantState("listening", `Listening (${chunksSent} chunks sent)`);
        }
      }
    });

    isStreaming = true;
    setAssistantState("listening");
  } catch (err) {
    console.error("[Microphone] Error starting stream:", err);
    showToast(err.message || "Failed to access microphone.");
    stopStreaming();
  }
}

function stopStreaming() {
  if (audioStreamer) audioStreamer.stop();
  if (websocket && websocket.readyState === WebSocket.OPEN && !conversationSwitchInProgress) {
    try { websocket.send(JSON.stringify({ type: "stop_audio" })); } catch (_) {}
  }

  isStreaming = false;
  currentVoiceEnergy = 0.0;
  if (conversationSwitchInProgress) return;
  if (chunksSent > 0) setAssistantState("thinking", "Thinking...");
  else setAssistantState("idle");
}

async function handleToggleStreaming() {
  if (conversationSwitchInProgress) return;
  try {
    await ensureAudioPlayerReady();
  } catch (err) {
    showToast(err.message || "Audio is not ready yet.");
    return;
  }

  if (isStreaming) stopStreaming();
  else await startStreaming();
}

// ------------------------------------------------------------------------------
// Text input
// ------------------------------------------------------------------------------
async function handleSendText() {
  if (isSendingText || conversationSwitchInProgress) return;

  try {
    await ensureAudioPlayerReady();
  } catch (err) {
    showToast(err.message || "Audio is not ready yet.");
    return;
  }

  const text = (textInput?.value || "").trim();
  if (!text) return;
  if (textInput) textInput.value = "";

  if (websocket && websocket.readyState === WebSocket.OPEN) {
    isSendingText = true;
    try {
      if (audioPlayer && audioPlayer.isPlaying) handleBargeIn("text_input");
      finalizeUserTurn();
      isUserTurnActive = false;
      currentUserTranscript = "";
      finalUserTranscript = "";
      pendingSentText = text;

      const bubble = getOrCreateUserBubble();
      bubble.textContent = text;
      bubble.classList.remove("turn-interim");
      currentUserBubble = null;
      currentAssistantBubble = null;

      setAssistantState("thinking", "Sending prompt...");
      websocket.send(JSON.stringify({ type: "text", text }));
      scrollConversationToBottom();
    } finally {
      isSendingText = false;
      if (textInput) textInput.value = "";
    }
    return;
  }

  showToast("WebSocket is not connected. Attempting reconnect...");
  initWebSocket();
}

function handleTextFormSubmit(event) {
  event.preventDefault();
  handleSendText();
}

async function handleTextInputKeydown(event) {
  if (event.key === "Enter") {
    event.preventDefault();
    await handleSendText();
  }
}

function unlockAudioOnFirstGesture() {
  ensureAudioPlayerReady().catch((err) => {
    console.warn("[AudioPlayer] Initial unlock failed:", err);
  });
}

function bindEventListeners() {
  if (eventListenersBound) return;
  eventListenersBound = true;

  if (clearConversationBtn) clearConversationBtn.addEventListener("click", handleClearConversation);
  if (recordButton) recordButton.addEventListener("click", handleToggleStreaming);
  if (voiceOrb) voiceOrb.addEventListener("click", handleToggleStreaming);
  if (textButton) textButton.addEventListener("click", handleSendText);
  if (textForm) textForm.addEventListener("submit", handleTextFormSubmit);
  if (textInput) textInput.addEventListener("keydown", handleTextInputKeydown);
  document.addEventListener("click", unlockAudioOnFirstGesture, { once: true });
}

// ------------------------------------------------------------------------------
// Safe one-time application initialization
// ------------------------------------------------------------------------------
function initializeApplication() {
  if (appInitialized) return;
  appInitialized = true;

  bindDomElements();
  bindEventListeners();
  initializeAudioSubsystem();
  initWaveform();
  syncTurnCountFromDom();

  if (!getAppConfig()) {
    setControlsEnabled(false);
    setConnectionState("disconnected", "Sign in required");
    setAssistantState("idle", "Sign in to start assistant");
    return;
  }

  setAssistantState("idle");
  setConnectionState("connecting", "Connecting...");
  initWebSocket();
  console.log("[App] Initialized.");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeApplication, { once: true });
} else {
  initializeApplication();
}
