(() => {
  const BACKEND = (location.hostname === "localhost" || location.hostname === "127.0.0.1")
    ? location.origin
    : "https://real-time-voice-assistant-9bh1.onrender.com";

  const TOKEN_KEY = "voiceAssistantToken";
  const USER_KEY = "voiceAssistantUser";
  const CONVERSATION_KEY = "voiceAssistantConversationId";
  const SELECT_LATEST_ON_BOOT_KEY = "voiceAssistantSelectLatestOnBoot";
  const AUTH_RETRY_DELAY_MS = 2500;

  let resolveAuthReady;
  let authResultPublished = false;
  let conversationSwitchSequence = 0;
  let activeConversationId = null;
  let knownConversations = [];

  window.VOICE_AUTH_STATE = {
    ready: false,
    authenticated: false,
    user: null,
    conversationId: null,
  };

  window.VOICE_AUTH_READY = new Promise((resolve) => {
    resolveAuthReady = resolve;
  });

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function updateStartupStatus(text) {
    const connectionText = document.getElementById("connectionText");
    const heroPrompt = document.getElementById("heroPrompt");
    const recordStatus = document.getElementById("recordStatus");
    if (connectionText) connectionText.textContent = text;
    if (heroPrompt) heroPrompt.textContent = `"${text}"`;
    if (recordStatus) recordStatus.textContent = text;
  }

  function clearStoredSession() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(CONVERSATION_KEY);
    localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
    activeConversationId = null;
    knownConversations = [];
    delete window.APP_CONFIG;
  }

  function configureAuthenticatedWebSocket(token, conversationId) {
    const params = new URLSearchParams({
      token,
      conversation_id: conversationId,
    });

    window.APP_CONFIG = {
      API_URL: BACKEND,
      WS_URL: `${BACKEND.replace(/^http/, "ws")}/ws/voice?${params.toString()}`,
    };
  }

  function publishAuthResult(authenticated, user = null, conversationId = null) {
    if (authResultPublished) return;
    authResultPublished = true;

    window.VOICE_AUTH_STATE = {
      ready: true,
      authenticated,
      user,
      conversationId,
    };

    resolveAuthReady(window.VOICE_AUTH_STATE);
  }

  function authHeaders(tokenOverride = null) {
    const token = tokenOverride || localStorage.getItem(TOKEN_KEY) || "";
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    };
  }

  async function api(path, options = {}) {
    let response;

    try {
      response = await fetch(`${BACKEND}${path}`, {
        ...options,
        cache: options.cache || "no-store",
        headers: {
          ...(options.headers || {}),
          ...(options.auth === false
            ? { "Content-Type": "application/json" }
            : authHeaders(options.tokenOverride || null)),
        },
      });
    } catch (cause) {
      const error = new Error("Unable to reach the server.");
      error.status = 0;
      error.cause = cause;
      throw error;
    }

    let data = {};
    try {
      data = await response.json();
    } catch (_) {}

    if (!response.ok) {
      const error = new Error(data.detail || "Request failed.");
      error.status = response.status;
      throw error;
    }

    return data;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>'"]/g, (c) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;",
    }[c]));
  }

  function injectStyles() {
    if (document.getElementById("voice-auth-styles")) return;

    const style = document.createElement("style");
    style.id = "voice-auth-styles";
    style.textContent = `
      .auth-overlay{position:fixed;inset:0;z-index:9999;background:#07101f;display:flex;align-items:center;justify-content:center;padding:24px;font-family:'Plus Jakarta Sans',sans-serif}
      .auth-card{width:min(430px,100%);background:#0f172a;border:1px solid #24324a;border-radius:22px;padding:30px;box-shadow:0 30px 80px rgba(0,0,0,.45)}
      .auth-logo{font-size:12px;font-weight:800;letter-spacing:.16em;color:#38bdf8;margin-bottom:8px}.auth-title{color:#f8fafc;font-size:28px;margin:0 0 8px}.auth-sub{color:#94a3b8;font-size:14px;margin-bottom:24px}
      .auth-tabs{display:flex;background:#0b1220;border-radius:12px;padding:4px;margin-bottom:20px}.auth-tab{flex:1;border:0;border-radius:9px;padding:10px;background:transparent;color:#94a3b8;font-weight:700;cursor:pointer}.auth-tab.active{background:#1d4ed8;color:white}
      .auth-field{display:block;margin:12px 0}.auth-field span{display:block;color:#cbd5e1;font-size:13px;margin-bottom:7px}.auth-field input{width:100%;box-sizing:border-box;padding:12px 13px;border-radius:11px;border:1px solid #334155;background:#0b1220;color:white;outline:none}.auth-field input:focus{border-color:#38bdf8}
      .auth-submit{width:100%;margin-top:10px;padding:13px;border:0;border-radius:11px;background:#2563eb;color:white;font-weight:800;cursor:pointer}.auth-submit:disabled{opacity:.65;cursor:wait}.auth-error{min-height:18px;color:#fb7185;font-size:13px;margin-top:10px}
      .history-sidebar{position:fixed;left:0;top:0;bottom:0;width:245px;background:#0a1220;border-right:1px solid #1e293b;z-index:40;padding:18px 14px;box-sizing:border-box;overflow:auto;font-family:'Plus Jakarta Sans',sans-serif}
      .history-brand{color:white;font-weight:800;font-size:14px;margin-bottom:16px}.history-new,.history-reminders{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #334155;background:#111c31;color:#e2e8f0;text-align:left;cursor:pointer;margin-bottom:8px;font-weight:700}
      .history-label{color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.08em;margin:18px 6px 8px}.history-item{padding:10px;border-radius:9px;color:#cbd5e1;font-size:13px;cursor:pointer;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.history-item:hover,.history-item.active{background:#17233a;color:white}.history-item.switching{opacity:.7;cursor:wait}
      .history-user{margin-top:18px;padding-top:14px;border-top:1px solid #1e293b;color:#cbd5e1;font-size:12px}.history-user strong{display:block;color:white;margin-bottom:3px}.history-logout{margin-top:9px;border:0;background:transparent;color:#f87171;padding:0;cursor:pointer}
      body.has-history-sidebar .app-layout{margin-left:245px;width:calc(100% - 245px)}
      .reminder-modal{position:fixed;inset:0;z-index:9998;background:rgba(2,6,23,.72);display:flex;align-items:center;justify-content:center;padding:20px}.reminder-card{width:min(520px,100%);max-height:70vh;overflow:auto;background:#0f172a;border:1px solid #334155;border-radius:18px;padding:22px;color:white}.reminder-row{padding:12px 0;border-bottom:1px solid #1e293b}.reminder-row small{display:block;color:#94a3b8;margin-top:4px}.reminder-close{float:right;border:0;background:#1e293b;color:white;border-radius:8px;padding:7px 10px;cursor:pointer}
      @media(max-width:900px){.history-sidebar{display:none}body.has-history-sidebar .app-layout{margin-left:0;width:100%}}
    `;
    document.head.appendChild(style);
  }

  function showAuth(message = "") {
    let overlay = document.querySelector(".auth-overlay");
    if (overlay) {
      const error = overlay.querySelector("#authError");
      if (error && message) error.textContent = message;
      return;
    }

    overlay = document.createElement("div");
    overlay.className = "auth-overlay";
    overlay.innerHTML = `
      <div class="auth-card">
        <div class="auth-logo">REAL-TIME VOICE ASSISTANT</div>
        <h2 class="auth-title">Welcome</h2>
        <div class="auth-sub">Login to keep your conversations and reminders private to your account.</div>
        <div class="auth-tabs"><button class="auth-tab active" data-mode="login">Login</button><button class="auth-tab" data-mode="register">Register</button></div>
        <form id="voiceAuthForm">
          <label class="auth-field" id="nameField" style="display:none"><span>Name</span><input id="authName" autocomplete="name"></label>
          <label class="auth-field"><span>Email</span><input id="authEmail" type="email" autocomplete="email" required></label>
          <label class="auth-field"><span>Password</span><input id="authPassword" type="password" autocomplete="current-password" required minlength="6"></label>
          <button class="auth-submit" type="submit">Login</button>
          <div class="auth-error" id="authError">${escapeHtml(message)}</div>
        </form>
      </div>`;
    document.body.appendChild(overlay);

    let mode = "login";

    overlay.querySelectorAll(".auth-tab").forEach((btn) => {
      btn.onclick = () => {
        mode = btn.dataset.mode;
        overlay.querySelectorAll(".auth-tab").forEach((b) => b.classList.toggle("active", b === btn));
        overlay.querySelector("#nameField").style.display = mode === "register" ? "block" : "none";
        overlay.querySelector(".auth-submit").textContent = mode === "register" ? "Create account" : "Login";
        overlay.querySelector("#authPassword").autocomplete = mode === "register" ? "new-password" : "current-password";
        overlay.querySelector("#authError").textContent = "";
      };
    });

    overlay.querySelector("#voiceAuthForm").onsubmit = async (event) => {
      event.preventDefault();
      const error = overlay.querySelector("#authError");
      const submit = overlay.querySelector(".auth-submit");
      error.textContent = "";
      submit.disabled = true;
      submit.textContent = mode === "register" ? "Creating..." : "Logging in...";

      try {
        const body = {
          email: overlay.querySelector("#authEmail").value.trim(),
          password: overlay.querySelector("#authPassword").value,
        };
        if (mode === "register") body.name = overlay.querySelector("#authName").value.trim();

        const result = await api(`/api/auth/${mode}`, {
          method: "POST",
          body: JSON.stringify(body),
          auth: false,
        });

        localStorage.setItem(TOKEN_KEY, result.access_token);
        localStorage.setItem(USER_KEY, JSON.stringify(result.user));
        localStorage.removeItem(CONVERSATION_KEY);
        localStorage.setItem(SELECT_LATEST_ON_BOOT_KEY, "1");
        location.reload();
      } catch (err) {
        error.textContent = err.message;
        submit.disabled = false;
        submit.textContent = mode === "register" ? "Create account" : "Login";
      }
    };
  }

  async function showReminders() {
    try {
      const data = await api("/api/reminders");
      const modal = document.createElement("div");
      modal.className = "reminder-modal";
      const rows = data.reminders?.length
        ? data.reminders.map((r) => `<div class="reminder-row"><strong>${escapeHtml(r.title)}</strong><small>${escapeHtml(r.remind_at)} · ${escapeHtml(r.status || "pending")}</small></div>`).join("")
        : `<p style="color:#94a3b8">No reminders yet. Try saying “Remind me to study DSA tomorrow at 7 PM.”</p>`;
      modal.innerHTML = `<div class="reminder-card"><button class="reminder-close">Close</button><h3>Your reminders</h3>${rows}</div>`;
      modal.querySelector(".reminder-close").onclick = () => modal.remove();
      modal.onclick = (event) => { if (event.target === modal) modal.remove(); };
      document.body.appendChild(modal);
    } catch (err) {
      alert(err.message);
    }
  }

  function renderEmptyConversation(list) {
    list.innerHTML = `
      <div class="empty-hint" id="emptyHint">
        <div class="empty-icon">🎙️</div>
        <p class="empty-title">Start this conversation</p>
        <p class="empty-desc">Ask by voice or text. Messages stay inside this conversation.</p>
      </div>`;
  }

  async function renderHistoryMessages(conversationId, expectedSwitchSequence = null) {
    const data = await api(`/api/conversations/${conversationId}/messages`);

    if (expectedSwitchSequence !== null && expectedSwitchSequence !== conversationSwitchSequence) {
      return { stale: true, turns: 0 };
    }

    const list = document.getElementById("conversationList");
    if (!list) return { stale: false, turns: 0 };

    const messages = Array.isArray(data.messages) ? data.messages : [];
    list.innerHTML = "";
    let turns = 0;

    if (messages.length === 0) {
      renderEmptyConversation(list);
    } else {
      for (const msg of messages) {
        const div = document.createElement("div");
        div.className = `turn ${msg.role === "user" ? "user-turn" : "assistant-turn"}`;
        div.innerHTML = `<div class="turn-header-row"><span class="turn-role-tag">${msg.role === "user" ? "USER" : "ASSISTANT"}</span></div><div class="turn-bubble"></div>`;
        div.querySelector(".turn-bubble").textContent = msg.text;
        list.appendChild(div);
        if (msg.role === "user") turns += 1;
      }
      list.scrollTop = list.scrollHeight;
    }

    const counter = document.getElementById("turnCounter");
    if (counter) counter.textContent = `${turns} ${turns === 1 ? "turn" : "turns"}`;
    if (typeof window.VOICE_APP_SYNC_TURN_COUNT === "function") {
      window.VOICE_APP_SYNC_TURN_COUNT(turns);
    }

    return { stale: false, turns };
  }

  function highlightActiveConversation(conversationId, switching = false) {
    document.querySelectorAll(".history-item[data-conversation-id]").forEach((item) => {
      const isActive = item.dataset.conversationId === conversationId;
      item.classList.toggle("active", isActive);
      item.classList.toggle("switching", switching && isActive);
    });
  }

  async function switchConversation(conversationId) {
    if (!conversationId || conversationId === activeConversationId) {
      highlightActiveConversation(activeConversationId, false);
      return;
    }

    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) return;

    const switchSequence = ++conversationSwitchSequence;
    const previousConversationId = activeConversationId;
    const previousConfig = window.APP_CONFIG ? { ...window.APP_CONFIG } : null;

    // Step 1: persist the requested active conversation immediately.
    localStorage.setItem(CONVERSATION_KEY, conversationId);
    localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
    activeConversationId = conversationId;
    if (window.VOICE_AUTH_STATE?.ready) {
      window.VOICE_AUTH_STATE.conversationId = conversationId;
    }
    highlightActiveConversation(conversationId, true);

    // Stop old-conversation traffic before loading the new history. This prevents
    // late packets from the old socket from being appended to the new thread.
    if (typeof window.VOICE_APP_BEGIN_CONVERSATION_SWITCH === "function") {
      window.VOICE_APP_BEGIN_CONVERSATION_SWITCH();
    }

    updateStartupStatus("Loading conversation...");

    try {
      // Steps 2-3: load and render only this conversation's stored messages.
      const history = await renderHistoryMessages(conversationId, switchSequence);
      if (history.stale || switchSequence !== conversationSwitchSequence) return;

      // Step 4: only after history is ready, publish the new authenticated WS URL
      // and let app.js establish one fresh socket for this conversation.
      configureAuthenticatedWebSocket(token, conversationId);
      highlightActiveConversation(conversationId, false);

      if (typeof window.VOICE_APP_COMPLETE_CONVERSATION_SWITCH === "function") {
        window.VOICE_APP_COMPLETE_CONVERSATION_SWITCH({ turnCount: history.turns });
      }

      updateStartupStatus("Connecting conversation...");
    } catch (err) {
      if (switchSequence !== conversationSwitchSequence) return;

      console.warn("Could not switch conversation", err);

      // Roll back to the previous conversation without mixing UI/socket state.
      activeConversationId = previousConversationId;
      if (previousConversationId) {
        localStorage.setItem(CONVERSATION_KEY, previousConversationId);
        if (window.VOICE_AUTH_STATE?.ready) {
          window.VOICE_AUTH_STATE.conversationId = previousConversationId;
        }
      }

      if (previousConfig) window.APP_CONFIG = previousConfig;
      highlightActiveConversation(previousConversationId, false);

      let rollbackTurns = 0;
      if (previousConversationId) {
        try {
          const rollback = await renderHistoryMessages(previousConversationId, switchSequence);
          rollbackTurns = rollback.turns;
        } catch (rollbackError) {
          console.warn("Could not restore previous conversation history", rollbackError);
        }
      }

      if (typeof window.VOICE_APP_CANCEL_CONVERSATION_SWITCH === "function") {
        window.VOICE_APP_CANCEL_CONVERSATION_SWITCH({ turnCount: rollbackTurns });
      }

      updateStartupStatus("Could not switch conversation");
    }
  }

  function renderAuthenticatedSidebar(user, conversations, active) {
    document.body.classList.add("has-history-sidebar");
    knownConversations = conversations;
    activeConversationId = active;

    const existing = document.querySelector(".history-sidebar");
    if (existing) existing.remove();

    const sidebar = document.createElement("aside");
    sidebar.className = "history-sidebar";
    sidebar.innerHTML = `
      <div class="history-brand">🎙️ Voice Assistant</div>
      <button class="history-new">＋ New chat</button>
      <button class="history-reminders">⏰ Reminders</button>
      <div class="history-label">Conversations</div>
      <div class="history-list"></div>
      <div class="history-user"><strong>${escapeHtml(user.name)}</strong>${escapeHtml(user.email)}<br><button class="history-logout">Log out</button></div>`;
    document.body.appendChild(sidebar);

    const list = sidebar.querySelector(".history-list");
    conversations.forEach((conversation) => {
      const item = document.createElement("div");
      item.className = `history-item ${conversation.id === active ? "active" : ""}`;
      item.dataset.conversationId = conversation.id;
      item.textContent = conversation.title || "New conversation";
      item.title = conversation.title || "New conversation";
      item.onclick = () => switchConversation(conversation.id);
      list.appendChild(item);
    });

    sidebar.querySelector(".history-new").onclick = async () => {
      const created = await api("/api/conversations", {
        method: "POST",
        body: JSON.stringify({ title: "New conversation" }),
      });
      localStorage.setItem(CONVERSATION_KEY, created.conversation.id);
      localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
      location.reload();
    };

    sidebar.querySelector(".history-reminders").onclick = showReminders;
    sidebar.querySelector(".history-logout").onclick = () => {
      clearStoredSession();
      location.reload();
    };
  }

  async function selectConversationForSession(conversations) {
    // Backend returns conversations newest-first by updated_at.
    if (conversations.length > 0) {
      const forceLatest = localStorage.getItem(SELECT_LATEST_ON_BOOT_KEY) === "1";
      const storedId = localStorage.getItem(CONVERSATION_KEY);
      const storedExists = storedId && conversations.some((conversation) => conversation.id === storedId);

      const selected = forceLatest || !storedExists
        ? conversations[0]
        : conversations.find((conversation) => conversation.id === storedId);

      localStorage.setItem(CONVERSATION_KEY, selected.id);
      localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
      return { conversation: selected, conversations };
    }

    // Zero server conversations: this is the only automatic creation path.
    const created = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({ title: "New conversation" }),
    });

    localStorage.setItem(CONVERSATION_KEY, created.conversation.id);
    localStorage.removeItem(SELECT_LATEST_ON_BOOT_KEY);
    return { conversation: created.conversation, conversations: [created.conversation] };
  }

  async function resolveAuthenticatedSession() {
    const token = localStorage.getItem(TOKEN_KEY);

    if (!token) {
      clearStoredSession();
      showAuth();
      updateStartupStatus("Sign in to start assistant");
      publishAuthResult(false);
      return;
    }

    updateStartupStatus("Checking your session...");

    while (true) {
      try {
        const me = await api("/api/auth/me");
        localStorage.setItem(USER_KEY, JSON.stringify(me.user));

        updateStartupStatus("Loading conversations...");
        const data = await api("/api/conversations");
        const serverConversations = Array.isArray(data.conversations) ? data.conversations : [];
        const selection = await selectConversationForSession(serverConversations);
        const active = selection.conversation.id;
        const conversations = selection.conversations;

        activeConversationId = active;
        knownConversations = conversations;
        configureAuthenticatedWebSocket(token, active);
        renderAuthenticatedSidebar(me.user, conversations, active);
        await renderHistoryMessages(active);

        updateStartupStatus("Session ready — starting assistant...");
        publishAuthResult(true, me.user, active);
        return;
      } catch (err) {
        if (err.status === 401 || err.status === 403) {
          console.warn("Stored session is no longer valid.");
          clearStoredSession();
          showAuth("Your session expired. Please log in again.");
          updateStartupStatus("Sign in to start assistant");
          publishAuthResult(false);
          return;
        }

        console.warn("Authentication initialization delayed:", err);
        updateStartupStatus(navigator.onLine
          ? "Restoring your session — still retrying..."
          : "Waiting for network connection...");
        await sleep(AUTH_RETRY_DELAY_MS);
      }
    }
  }

  function bootAuth() {
    injectStyles();
    resolveAuthenticatedSession();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootAuth, { once: true });
  } else {
    bootAuth();
  }
})();
