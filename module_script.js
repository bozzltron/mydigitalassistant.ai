
import { $, $$, api, scrollBottom, formatBytes, showToast } from '/static/shared/utils.js';

(function() {
  'use strict';

  // ---- State ----
  const state = {
    userId: null,
    sessionId: localStorage.getItem('session_id') || null,
    userName: '',
    messages: [],
    voiceMode: false,
    mediaRecorder: null,
    audioChunks: [],
    isRecording: false,
    currentMimeType: null,
    lastCitations: [],
    ogCache: {},  // url -> {title, description, image, site_name}
    isTurnActive: false,
    messageQueue: [],  // {text, el} — sent sequentially after the active turn
    dictation: false,  // one-shot mic recording → populate input (no auto-send)
    braveEnabled: false,
    braveConfigured: false,
  };

  // ---- Settings (localStorage) ----
  function getSettings() {
    return {
      ttsEnabled: localStorage.getItem('tts_enabled') === 'true',
      voiceUri: localStorage.getItem('voice_uri'),
      traceVisible: localStorage.getItem('trace_visible') !== 'false',
      voiceMode: localStorage.getItem('voice_mode') === 'true',
      voiceSpeed: parseFloat(localStorage.getItem('voice_speed')) || 1.0,
      voicePitch: parseFloat(localStorage.getItem('voice_pitch')) || 1.0,
      voiceVolume: parseFloat(localStorage.getItem('voice_volume')) || 1.0,
    };
  }

  function saveSettings(s) {
    if ('ttsEnabled' in s) localStorage.setItem('tts_enabled', String(s.ttsEnabled));
    if ('voiceUri' in s) localStorage.setItem('voice_uri', s.voiceUri);
    if ('traceVisible' in s) localStorage.setItem('trace_visible', String(s.traceVisible));
    if ('voiceMode' in s) localStorage.setItem('voice_mode', String(s.voiceMode));
    if ('voiceSpeed' in s) localStorage.setItem('voice_speed', String(s.voiceSpeed));
    if ('voicePitch' in s) localStorage.setItem('voice_pitch', String(s.voicePitch));
    if ('voiceVolume' in s) localStorage.setItem('voice_volume', String(s.voiceVolume));
    applySettings();
  }

  function applySettings() {
    const s = getSettings();
    const ttsEnabled = $('#tts-enabled');
    const traceVisible = $('#trace-visible');
    const tracePanel = $('#trace-panel');
    const voiceModeBtn = $('#voice-mode-btn');
    
    if (ttsEnabled) ttsEnabled.checked = s.ttsEnabled;
    if (traceVisible) traceVisible.checked = s.traceVisible;
    if (tracePanel) tracePanel.classList.toggle('hidden', !s.traceVisible);
    if (voiceModeBtn) voiceModeBtn.classList.toggle('active', s.voiceMode);
    
    // Apply voice settings
    const voiceSpeed = $('#voice-speed');
    const voicePitch = $('#voice-pitch'); 
    const voiceVolume = $('#voice-volume');
    const speedValue = $('#voice-speed-value');
    const pitchValue = $('#voice-pitch-value');
    const volumeValue = $('#voice-volume-value');
    
    if (voiceSpeed) voiceSpeed.value = s.voiceSpeed;
    if (voicePitch) voicePitch.value = s.voicePitch;
    if (voiceVolume) voiceVolume.value = s.voiceVolume;
    
    if (speedValue) speedValue.textContent = `${s.voiceSpeed}x`;
    if (pitchValue) pitchValue.textContent = `${s.voicePitch}x`;
    if (volumeValue) volumeValue.textContent = s.voiceVolume;
  }

  // ---- Conversation management ----
  let conversationsInitialized = false;

  function initConversations() {
    // Populate conversation dropdown with user sessions
    if (state.userId) {
      api(`/users/${state.userId}/sessions`).then(sessions => {
        const select = $('#conversation-select');
        if (select) {
          // Clear existing options (except the default)
          const existingOptions = select.querySelectorAll('option:not(:first-child)');
          existingOptions.forEach(opt => opt.remove());
          
          // Add sessions to dropdown
          sessions.forEach(sess => {
            const label = sess.last_message || `Conversation ${sess.episode_count}`;
            const option = document.createElement('option');
            option.value = sess.id;
            option.textContent = label;
            select.appendChild(option);
          });
          
          // Select the current conversation if set, otherwise first one
          if (state.conversationId) {
            select.value = state.conversationId;
          } else if (sessions.length > 0) {
            select.value = sessions[0].id;
          }
        }
      }).catch(_ => { /* best-effort */ });
    }
    
    // Event listeners for conversation management (only once)
    if (!conversationsInitialized) {
      const select = $('#conversation-select');
      if (select) {
        select.addEventListener('change', (e) => {
          const convId = e.target.value;
          if (convId) {
            switchConversation(convId);
          }
        });
      }
      
      const newBtn = $('#new-conversation-btn');
      if (newBtn) {
        newBtn.addEventListener('click', createNewConversation);
      }
      conversationsInitialized = true;
    }
  }

  async function switchConversation(convId) {
    state.conversationId = convId;
    state.sessionId = convId;  // Keep in sync
    localStorage.setItem('session_id', convId);
    if (state.userId) {
      try {
        const history = await api(
          `/chat/session/${encodeURIComponent(convId)}/messages?user_id=${state.userId}&limit=50`
        );
        state.messages = [];
        // Clear all messages from DOM except welcome
        const messagesContainer = $('#messages');
        if (messagesContainer) {
          const msgs = messagesContainer.querySelectorAll('.msg');
          msgs.forEach(m => m.remove());
        }
        const welcome = $('#welcome');
        if (welcome) welcome.remove();
        for (const m of history) {
          state.messages.push({ role: m.role, content: m.content });
          addMessage(m.role, m.content);
        }
        if (history.length) messagesEl.scrollTop = messagesEl.scrollHeight;
      } catch (_) { /* best-effort */ }
    }
  }

  function createNewConversation() {
    if (!state.userId) return;
    const name = prompt('Conversation name (optional):');
    api(`/conversations/new?user_id=${state.userId}`, {
      method: 'POST',
    }).then(resp => {
      const sessionId = resp.session_id;
      if (name && name.trim()) {
        // Update session title via API
        fetch(`${BASE}/conversations/${encodeURIComponent(sessionId)}/title`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ user_id: state.userId, title: name.trim() })
        }).catch(_ => {});
      }
      state.sessionId = sessionId;
      localStorage.setItem('session_id', sessionId);
      state.conversationId = sessionId;
      switchConversation(sessionId);
      initConversations();  // Refresh dropdown to show new session
    }).catch(_ => {});
  }

  // ---- DOM refs ----
  const messagesEl = $('#messages');
  const msgInput = $('#msg-input');
  const sendBtn = $('#send-btn');
  const micBtn = $('#mic-btn');
  const userBadge = $('#user-badge');
  const tracePanel = $('#trace-panel');
  const traceTaskType = $('#trace-task-type');
  const traceMemory = $('#trace-memory');
  const traceCitations = $('#trace-citations');
  const traceSearchSection = $('#trace-search-section');
  const traceSearchInfo = $('#trace-search-info');
  const voiceOverlay = $('#voice-overlay');
  const voiceStatus = $('#voice-status');
  const voiceStopBtn = $('#voice-stop');
  const voiceCancelBtn = $('#voice-cancel');
  const voiceStatusBar = $('#voice-status-bar');
  const voiceStatusDot = $('#voice-status-dot');
  const voiceStopInline = $('#voice-stop-inline');
  const voiceCancelInline = $('#voice-cancel-inline');
  const voiceModeBtn = $('#voice-mode-btn');
  const stopSpeakingBtn = $('#stop-speaking-btn');
  const settingsPanel = $('#settings-panel');
  const settingsToggle = $('#settings-toggle');
  const settingsClose = $('#settings-close');
  const ttsEnabled = $('#tts-enabled');
  const voiceSelect = $('#voice-select');
  const traceVisible = $('#trace-visible');

  // ---- Backend URL ----
  const BASE = window.location.origin;

  async function fetchLinkPreviews(urls, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    // Render placeholder cards first
    container.innerHTML = urls.map(u => {
      const hostname = new URL(u).hostname;
      return `<a href="${u}" target="_blank" rel="noopener" class="preview-card">
        <div class="preview-card-img-placeholder"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></div>
        <div class="preview-card-body">
          <div class="preview-card-site">${hostname}</div>
          <div class="preview-card-title">${u}</div>
          <div class="preview-card-url">${u}</div>
        </div>
      </a>`;
    }).join('');

    // Fetch OG metadata for each URL (up to 5)
    const toFetch = urls.slice(0, 5);
    const cards = await Promise.all(toFetch.map(async (url) => {
      if (state.ogCache[url]) {
        return { url, ...state.ogCache[url] };
      }
      try {
        const resp = await api(`/og-preview?url=${encodeURIComponent(url)}`);
        if (resp.found) {
          state.ogCache[url] = {
            title: resp.title || '',
            description: resp.description || '',
            image: resp.image || '',
            site_name: resp.site_name || '',
          };
          return { url, ...state.ogCache[url] };
        }
      } catch (e) {
        // Ignore fetch errors, fall back to plain card
      }
      return { url, found: false };
    }));

    // Re-render with fetched data
    container.innerHTML = urls.map((u, i) => {
      const card = cards[i] || {};
      const hostname = new URL(u).hostname;
      const siteName = card.site_name || hostname;
      const title = card.title || u;
      const img = card.image;

      if (img) {
        return `<a href="${u}" target="_blank" rel="noopener" class="preview-card">
          <img src="${img}" alt="" class="preview-card-img" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"/>
          <div class="preview-card-img-placeholder" style="display:none"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></div>
          <div class="preview-card-body">
            <div class="preview-card-site">${siteName}</div>
            <div class="preview-card-title">${title}</div>
            <div class="preview-card-url">${hostname}</div>
          </div>
        </a>`;
      }
      return `<a href="${u}" target="_blank" rel="noopener" class="preview-card">
        <div class="preview-card-img-placeholder"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></div>
        <div class="preview-card-body">
          <div class="preview-card-site">${siteName}</div>
          <div class="preview-card-title">${title}</div>
          <div class="preview-card-url">${hostname}</div>
        </div>
      </a>`;
    }).join('');
  }

  function copyMessage(div, btn, text) {
    // Strip markdown formatting for cleaner copy
    const plain = text
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[#*`_\[\]]/g, '')
      .replace(/\n+/g, ' ')
      .trim();
    navigator.clipboard.writeText(plain).then(() => {
      btn.classList.add('copied');
      setTimeout(() => btn.classList.remove('copied'), 1500);
    }).catch(() => {});
  }

  async function submitReaction(kind, msgId) {
    try {
      await api('/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          episode_id: state.sessionId || null,
          message_id: msgId,
          kind,
          comment: null,
        }),
      });
    } catch (e) {
      // Silently fail - reaction is best-effort
    }
  }

  function showCorrectionPanel(div, msgId) {
    // Remove any existing correction panel
    const existing = div.querySelector('.correction-panel');
    if (existing) { existing.remove(); return; }

    const panel = document.createElement('div');
    panel.className = 'correction-panel';
    panel.innerHTML = `
      <textarea placeholder="What should I have said? Or what do you want to correct?"></textarea>
      <div class="correction-actions">
        <button class="correction-cancel">Cancel</button>
        <button class="correction-submit">Submit Correction</button>
      </div>
    `;

    const textarea = panel.querySelector('textarea');
    const cancelBtn = panel.querySelector('.correction-cancel');
    const submitBtn = panel.querySelector('.correction-submit');

    cancelBtn.addEventListener('click', () => panel.remove());

    submitBtn.addEventListener('click', async () => {
      const comment = textarea.value.trim();
      if (!comment) return;
      try {
        const result = await api('/correction', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            episode_id: state.sessionId || null,
            message_id: msgId,
            correction_text: comment,
          }),
        });
        if (result.status && !result.status.startsWith('Could not')) {
          panel.innerHTML = `<div class="correction-thanks">${result.status}</div>`;
        } else {
          panel.innerHTML = `<div class="correction-thanks">${result.status || 'Thanks for the correction!'}</div>`;
        }
        setTimeout(() => panel.remove(), 3000);
      } catch (e) {
        panel.remove();
      }
    });

    div.appendChild(panel);
    textarea.focus();
  }

  function addMessage(role, content, meta = {}) {
    const welcome = $('#welcome');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = `msg msg-${role}`;

    const msgId = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'content';
    contentDiv.innerHTML = marked.parse(content || '');
    div.appendChild(contentDiv);

    if (meta.task_type) {
      const metaEl = document.createElement('div');
      metaEl.className = 'msg-meta';
      metaEl.textContent = `type: ${meta.task_type}`;
      div.appendChild(metaEl);
    }

    // Render inline og:images in assistant message (search results only)
    if (role === 'assistant' && meta.ogData && meta.task_type === 'search') {
      const urlsWithImages = Object.entries(meta.ogData).filter(([, d]) => d && d.image);
      if (urlsWithImages.length > 0) {
        const imgsDiv = document.createElement('div');
        imgsDiv.className = 'msg-images';
        imgsDiv.innerHTML = urlsWithImages.map(([url, data]) => {
          const siteName = data.site_name || new URL(url).hostname;
          return `<a href="${url}" target="_blank" rel="noopener">
            <img src="${data.image}" alt="" loading="lazy" onerror="this.parentElement.remove()"/>
            <div class="img-site">${siteName}</div>
          </a>`;
        }).join('');
        div.appendChild(imgsDiv);
      }
    }

    // "What I learned / corrected" indicator (assistant messages only)
    if (role === 'assistant') {
      const summary = meta.extraction_summary || meta.search_extraction_summary;
      const slots = summary?.slots;
      if (slots && slots.length > 0) {
        const indicator = document.createElement('details');
        indicator.className = 'learned-indicator';

        const isSearch = meta.search_extraction_summary?.slots?.length > 0;
        const learnedCount = (meta.extraction_summary?.slots || []).length;
        const searchCount = (meta.search_extraction_summary?.slots || []).length;
        const conflictCount = slots.filter(s => s.conflict).length;

        let label = 'What I learned';
        if (conflictCount > 0) label += ` (${conflictCount} auto-resolved)`;
        if (isSearch) label = 'Found from search';

        // Backend indicator badge for search-driven messages
        let backendBadge = '';
        if (isSearch && meta.search_info) {
          const backend = meta.search_info.backend;
          const badgeClass = backend === 'brave' ? 'badge-brave' : 'badge-searxng';
          const badgeLabel = backend === 'brave' ? 'Searched via Brave' : 'Searched via local SearXNG';
          backendBadge = `<span class="badge ${badgeClass}" style="margin-left:0.4rem;font-size:0.65rem;">${badgeLabel}</span>`;
        }

        indicator.innerHTML = `<summary>${label}${backendBadge}</summary><div class="learned-items"></div>`;
        const itemsDiv = indicator.querySelector('.learned-items');

        slots.forEach(slot => {
          const item = document.createElement('div');
          item.className = 'learned-item';
          if (slot.conflict) {
            item.classList.add('kind-conflict');
            item.textContent = `Auto-resolved: ${slot.frame_name} → ${slot.key}: ${slot.value}`;
          } else if (isSearch) {
            item.classList.add('kind-search');
            item.textContent = `${slot.frame_name} → ${slot.key}: ${slot.value}`;
          } else {
            item.classList.add('kind-learned');
            item.textContent = `${slot.frame_name} → ${slot.key}: ${slot.value}`;
          }
          itemsDiv.appendChild(item);
        });

        div.appendChild(indicator);
      }
    }

    // Message actions bar
    const actionsDiv = document.createElement('div');
    actionsDiv.className = 'msg-actions';

    // Copy button (all messages)
    const copyBtn = document.createElement('button');
    copyBtn.className = 'msg-action-btn';
    copyBtn.title = 'Copy message';
    copyBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>`;
    copyBtn.addEventListener('click', () => copyMessage(div, copyBtn, content));
    actionsDiv.appendChild(copyBtn);

    // Reaction buttons (assistant messages only)
    if (role === 'assistant') {
      const reactThumbsUp = document.createElement('button');
      reactThumbsUp.className = 'reaction-btn';
      reactThumbsUp.title = 'This was good';
      reactThumbsUp.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>`;
      reactThumbsUp.addEventListener('click', () => submitReaction('positive', msgId));
      actionsDiv.appendChild(reactThumbsUp);

      const reactThumbsDown = document.createElement('button');
      reactThumbsDown.className = 'reaction-btn';
      reactThumbsDown.title = 'This was wrong';
      reactThumbsDown.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg>`;
      reactThumbsDown.addEventListener('click', () => submitReaction('negative', msgId));
      actionsDiv.appendChild(reactThumbsDown);

      const reactFlag = document.createElement('button');
      reactFlag.className = 'reaction-btn';
      reactFlag.title = 'Flag / correct';
      reactFlag.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/><line x1="4" x2="4" y1="22" y2="15"/></svg>`;
      reactFlag.addEventListener('click', () => showCorrectionPanel(div, msgId));
      actionsDiv.appendChild(reactFlag);
    }

    div.appendChild(actionsDiv);

    messagesEl.appendChild(div);
    scrollBottom();

    // TTS
    if (role === 'assistant' && getSettings().ttsEnabled && content) {
      speak(content);
    }

    // Save to history
    state.messages.push({ role, content, ...meta });

    // Update trace panel
    if (meta.task_type) traceTaskType.textContent = meta.task_type;
    if (meta.memory_context) traceMemory.textContent = meta.memory_context.slice(0, 500) + (meta.memory_context.length > 500 ? '...' : '');

    // Citations in trace panel — only for search task type
    if (meta.task_type === 'search' && meta.citations && meta.citations.length > 0) {
      if (role === 'assistant') {
        state.lastCitations = meta.citations;
      }
      traceCitations.innerHTML = `<span class="sources-header">Sources:</span><div class="preview-cards" id="preview-cards-container"></div>`;
      fetchLinkPreviews(meta.citations, 'preview-cards-container');
    } else {
      traceCitations.textContent = '-';
    }

    // Search transparency: backend + query + per-result engines
    if (meta.search_info && role === 'assistant') {
      const si = meta.search_info;
      const badge = `<span class="badge badge-${si.backend}">${si.backend}</span>`;
      const engines = [...new Set(si.results.map(r => r.engine))];
      const engineBadges = engines.map(e => `<span class="badge badge-${si.backend}">${e}</span>`).join(' ');
      traceSearchInfo.innerHTML = `${badge} <strong>${si.query}</strong><br/><small>Engines: ${engineBadges || '-'}</small>`;
      traceSearchSection.style.display = 'block';
    } else {
      traceSearchSection.style.display = 'none';
    }

    return div;
  }

  function appendFileMessage(file) {
    const div = document.createElement('div');
    div.className = 'msg msg-user';
    div.innerHTML = `
      <div class="content">
        <div class="file-attachment">
          <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 2C9.44772 2 9 2.44772 9 3V12H7V3C7 1.34315 8.34315 0 10 0C11.6569 0 13 1.34315 13 3V11C13 13.7614 10.7614 16 8 16C5.23858 16 3 13.7614 3 11V3.5H5V11C5 12.6569 6.34315 14 8 14C9.65685 14 11 12.6569 11 11V3C11 2.44772 10.5523 2 10 2Z"/>
          </svg>
          <span class="file-name">${file.name}</span>
          <span class="file-size">(${formatBytes(file.size)})</span>
        </div>
      </div>
    `;
    messagesEl.appendChild(div);
    scrollBottom();
    state.messages.push({ role: 'user', content: `[File attached: ${file.name}]` });
  }
    // Input stays enabled while a turn runs: typed messages are queued
    // and auto-sent when the turn completes.
    sendBtn.disabled = false;
    msgInput.disabled = false;
    sendBtn.title = loading ? 'Queue message' : 'Send';
    if (loading) {
      const div = document.createElement('div');
      div.className = 'msg msg-assistant';
      div.id = 'loading-msg';
      div.innerHTML = '<div class="content"><em>Reading your message<span class="loading-dots"></span></em></div>';
      messagesEl.appendChild(div);
      scrollBottom();
    } else {
      const el = $('#loading-msg');
      if (el) el.remove();
    }
  }

  function enqueueMessage(text) {
    const el = document.createElement('div');
    el.className = 'msg msg-user msg-queued';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'content';
    contentDiv.textContent = text;
    el.appendChild(contentDiv);

    const removeBtn = document.createElement('button');
    removeBtn.className = 'queued-remove';
    removeBtn.title = 'Remove queued message';
    removeBtn.innerHTML =
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
    removeBtn.addEventListener('click', () => {
      state.messageQueue = state.messageQueue.filter((q) => q.el !== el);
      el.remove();
    });
    el.appendChild(removeBtn);

    messagesEl.appendChild(el);
    scrollBottom();
    state.messageQueue.push({ text, el });
  }

  function drainMessageQueue() {
    const next = state.messageQueue.shift();
    if (!next) return;
    next.el.remove();
    sendMessage(next.text);
  }

  // ---- Settings (localStorage) ----
  function getSettings() {
    return {
      ttsEnabled: localStorage.getItem('tts_enabled') === 'true',
      voiceUri: localStorage.getItem('voice_uri'),
      traceVisible: localStorage.getItem('trace_visible') !== 'false',
      voiceMode: localStorage.getItem('voice_mode') === 'true',
      voiceSpeed: parseFloat(localStorage.getItem('voice_speed')) || 1.0,
      voicePitch: parseFloat(localStorage.getItem('voice_pitch')) || 1.0,
      voiceVolume: parseFloat(localStorage.getItem('voice_volume')) || 1.0,
    };
  }

  function saveSettings(s) {
    if ('ttsEnabled' in s) localStorage.setItem('tts_enabled', String(s.ttsEnabled));
    if ('voiceUri' in s) localStorage.setItem('voice_uri', s.voiceUri);
    if ('traceVisible' in s) localStorage.setItem('trace_visible', String(s.traceVisible));
    if ('voiceMode' in s) localStorage.setItem('voice_mode', String(s.voiceMode));
    applySettings();
  }

  function applySettings() {
    const s = getSettings();
    ttsEnabled.checked = s.ttsEnabled;
    traceVisible.checked = s.traceVisible;
    tracePanel.classList.toggle('hidden', !s.traceVisible);
    voiceModeBtn.classList.toggle('active', s.voiceMode);
    
    // Apply voice settings
    const voiceSpeed = $('#voice-speed');
    const voicePitch = $('#voice-pitch'); 
    const voiceVolume = $('#voice-volume');
    const speedValue = $('#voice-speed-value');
    const pitchValue = $('#voice-pitch-value');
    const volumeValue = $('#voice-volume-value');
    
    if (voiceSpeed) voiceSpeed.value = s.voiceSpeed;
    if (voicePitch) voicePitch.value = s.voicePitch;
    if (voiceVolume) voiceVolume.value = s.voiceVolume;
    
    if (speedValue) speedValue.textContent = `${s.voiceSpeed}x`;
    if (pitchValue) pitchValue.textContent = `${s.voicePitch}x`;
    if (volumeValue) volumeValue.textContent = s.voiceVolume;
  }

  // ---- Voice / TTS state machine ----
  const VoiceState = {
    IDLE: 'idle',
    LISTENING: 'listening',
    PROCESSING: 'processing',
    SPEAKING: 'speaking',
  };

  let voiceState = VoiceState.IDLE;
  let currentUtterance = null;
  let speakGeneration = 0;
  let bargeInGeneration = 0;

  // Audio analysis globals
  let mediaStream = null;
  let audioContext = null;
  let analyser = null;
  let silenceTimeout = null;
  let bargeInMonitorId = null;
  let recordingTimeoutId = null;
  let recordingStartTime = null;
  let loudFrameCount = 0;
  let silenceAfterLoud = false;
  const SILENCE_THRESHOLD = 0.08;
  const BARGE_IN_THRESHOLD = 0.14;
  const SILENCE_DURATION = 2000;
  const BARGE_IN_DURATION = 350;
  const MIN_RECORDING_MS = 500;
  const MIN_AUDIO_LEVEL = 0.015;
  const MIN_AUDIO_FRAMES = 3;
  const MAX_RECORDING_MS = 180000;
  let monitorIntervalId = null;
  const MONITOR_INTERVAL_MS = 80;

  function setVoiceState(newState, reason = '') {
    const oldState = voiceState;
    if (oldState === newState) return;
    voiceState = newState;

    const isActive = newState !== VoiceState.IDLE;

    // Update inline status bar
    voiceStatusBar.classList.toggle('active', isActive);
    voiceStatusDot.classList.remove('processing', 'speaking');
    if (newState === VoiceState.LISTENING) {
      voiceStatusDot.classList.remove('processing', 'speaking');
    } else if (newState === VoiceState.PROCESSING) {
      voiceStatusDot.classList.add('processing');
    } else if (newState === VoiceState.SPEAKING) {
      voiceStatusDot.classList.add('speaking');
    }

    const statusTexts = {
      [VoiceState.IDLE]: '',
      [VoiceState.LISTENING]: 'Listening',
      [VoiceState.PROCESSING]: reason || 'Thinking...',
      [VoiceState.SPEAKING]: 'Speaking',
    };
    $('#voice-status-text').textContent = statusTexts[newState] || '';

    // Mic button recording indicator
    micBtn.classList.toggle('recording', newState === VoiceState.LISTENING);

    // Legacy overlay — keep in DOM, hidden
    voiceOverlay.classList.toggle('active', false);

    switch (newState) {
      case VoiceState.IDLE:
        stopSpeakingBtn.style.display = 'none';
        break;
      case VoiceState.LISTENING:
        stopSpeakingBtn.style.display = 'none';
        break;
      case VoiceState.PROCESSING:
        stopSpeakingBtn.style.display = 'none';
        break;
      case VoiceState.SPEAKING:
        stopSpeakingBtn.style.display = 'inline-block';
        break;
    }
  }

  // ---- Audio feedback (earcons) ----
  function playEarcon(type) {
    if (!window.AudioContext && !window.webkitAudioContext) return;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      const now = ctx.currentTime;
      if (type === 'start') {
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, now);
        osc.frequency.exponentialRampToValueAtTime(1760, now + 0.08);
        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
        osc.start(now);
        osc.stop(now + 0.12);
      } else if (type === 'stop') {
        osc.type = 'sine';
        osc.frequency.setValueAtTime(1760, now);
        osc.frequency.exponentialRampToValueAtTime(880, now + 0.08);
        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
        osc.start(now);
        osc.stop(now + 0.12);
      } else if (type === 'error') {
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(300, now);
        gain.gain.setValueAtTime(0.06, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.25);
        osc.start(now);
        osc.stop(now + 0.25);
      }
      setTimeout(() => ctx.close(), 300);
    } catch (e) {
      // ignore
    }
  }

  function populateVoices() {
    const voices = speechSynthesis.getVoices();
    const localVoices = voices.filter(v => !v.url); // local voices only
    voiceSelect.innerHTML = '';
    if (localVoices.length === 0) {
      voiceSelect.innerHTML = '<option value="">No local voices found</option>';
      return;
    }
    const settings = getSettings();

    // Prefer known good voices (order matters)
    const preferredNames = [
      'Samantha',        // macOS US English
      'Google US English',
      'Microsoft Aria',   // Windows
      'Alex',            // macOS US English male
      'Karen',           // macOS Australian English
      'Victoria',        // macOS UK English
      'Moira',           // macOS Irish English
    ];

    let defaultIndex = -1;
    if (!settings.voiceUri) {
      // Try to find a preferred voice
      for (let i = 0; i < localVoices.length; i++) {
        const name = localVoices[i].name;
        if (preferredNames.some(p => name.includes(p))) {
          defaultIndex = i;
          break;
        }
      }
      if (defaultIndex === -1) defaultIndex = 0;
    }

    localVoices.forEach((v, i) => {
      const opt = document.createElement('option');
      opt.value = v.voiceURI;
      opt.textContent = `${v.name} (${v.lang})`;
      if (settings.voiceUri && v.voiceURI === settings.voiceUri) opt.selected = true;
      if (!settings.voiceUri && i === defaultIndex) opt.selected = true;
      voiceSelect.appendChild(opt);
    });
    if (!settings.voiceUri && defaultIndex >= 0) {
      saveSettings({ voiceUri: localVoices[defaultIndex].voiceURI });
    }
  }

  function speak(text) {
    if (!window.speechSynthesis) {
      // No TTS available; if in voice mode, go straight back to listening
      if (state.voiceMode) startListeningForVoice();
      return;
    }

    // Strip markdown links, URLs, and formatting
    let plain = text
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')  // [text](url) -> text
      .replace(/https?:\/\/\S+/g, '')              // URLs
      .replace(/www\.\S+/g, '')                    // www URLs
      .replace(/[#*`_\[\]]/g, '')                 // markdown chars
      .replace(/\n+/g, ' ')
      .trim();
    if (!plain) {
      if (state.voiceMode) startListeningForVoice();
      return;
    }

    const wasVoiceMode = state.voiceMode;
    setVoiceState(VoiceState.SPEAKING, 'tts starting');
    if (wasVoiceMode) {
      stopBargeInMonitor();
      startBargeInMonitor();
    }

    const gen = ++speakGeneration;
    speechSynthesis.cancel();

    const s = getSettings();
    const voices = speechSynthesis.getVoices();
    const voice = voices.find(v => v.voiceURI === s.voiceUri) || voices[0];
    if (!voice) {
      if (wasVoiceMode) startListeningForVoice();
      return;
    }

    const u = new SpeechSynthesisUtterance(plain);
    u.voice = voice;
    u.rate = s.voiceSpeed;
    u.pitch = s.voicePitch;
    u.volume = s.voiceVolume;
    currentUtterance = u;
    const myGen = gen;
    u.onstart = () => {
      if (myGen !== speakGeneration) return;
      if (wasVoiceMode) setVoiceState(VoiceState.SPEAKING, 'tts started');
    };
    u.onend = u.onerror = () => {
      stopSpeakingBtn.style.display = 'none';
      stopBargeInMonitor();
      if (myGen !== speakGeneration) {
        // Interrupted by stop button: restore idle state
        if (!state.voiceMode) setVoiceState(VoiceState.IDLE, 'tts stopped');
        return;
      }
      currentUtterance = null;
      if (state.voiceMode) {
        startListeningForVoice();
      } else {
        setVoiceState(VoiceState.IDLE, 'tts finished, voice mode off');
      }
    };
    speechSynthesis.speak(u);
  }

  function stopSpeaking() {
    if (window.speechSynthesis) {
      speechSynthesis.cancel();
    }
    currentUtterance = null;
  }

  // Start listening for voice input (in voice mode)
  function startListeningForVoice() {
    if (!state.voiceMode) return;
    setVoiceState(VoiceState.LISTENING, 'start listening');
    playEarcon('start');
    stopSpeaking();
    stopBargeInMonitor();
    startRecording();
  }

  // One-shot dictation: record a single utterance and put the transcript in
  // the message box for review — distinct from continuous conversation mode.
  function startDictation() {
    if (state.isRecording || state.voiceMode) return;
    state.dictation = true;
    setVoiceState(VoiceState.LISTENING, 'listening');
    playEarcon('start');
    startRecording();
  }

  function endDictation() {
    state.dictation = false;
    if (!state.voiceMode && voiceState !== VoiceState.SPEAKING) {
      setVoiceState(VoiceState.IDLE);
    }
  }

  function enterVoiceMode() {
    state.voiceMode = true;
    saveSettings({ voiceMode: true });
    playEarcon('start');
    startListeningForVoice();
  }

  function exitVoiceMode() {
    state.voiceMode = false;
    saveSettings({ voiceMode: false });
    speakGeneration++;   // invalidate pending TTS callbacks
    bargeInGeneration++; // invalidate pending barge-in callbacks
    stopBargeInMonitor();
    stopSpeaking();
    stopRecording();
    setVoiceState(VoiceState.IDLE, 'exit voice mode');
  }

  function isVoiceExitCommand(text) {
    const t = text.toLowerCase().trim();
    return /^(stop listening|exit voice mode|stop voice mode|goodbye|bye)$/.test(t);
  }

  // ---- Chat ----
  const STAGE_LABELS = {
    queued: 'getting started',
    routing: 'reading your message',
    recall: 'checking my memory',
    correcting: 'updating what I know',
    learning: 'learning from our conversation',
    searching: 'searching the web',
    reasoning: 'thinking it through',
    responding: 'writing a reply',
    scheduled: 'managing your daily list',
  };

  let stagePollTimer = null;

  function startStagePolling(turnId) {
    stopStagePolling();
    const el = () => document.querySelector('#loading-msg .content em');
    stagePollTimer = setInterval(async () => {
      try {
        const r = await fetch(`/chat/status/${turnId}`);
        if (!r.ok) return;
        const s = await r.json();
        if (s.done) { stopStagePolling(); return; }
        const label = STAGE_LABELS[s.stage] || s.detail || s.stage;
        const node = el();
        if (node) {
          node.innerHTML =
            `${label}<span class="loading-dots"></span>` +
            ` <span class="stage-elapsed">${Math.round(s.elapsed_s)}s</span>`;
        }
      } catch (_) { /* status is best-effort */ }
    }, 600);
  }

  function stopStagePolling() {
    if (stagePollTimer) { clearInterval(stagePollTimer); stagePollTimer = null; }
  }

  async function sendMessage(text) {
    if (!text.trim() || !state.userId) return;
    if (state.sessionId) {
      localStorage.setItem('session_id', state.sessionId);
    }

    // Turn in flight → queue instead of dropping or interleaving turns.
    if (state.isTurnActive) {
      enqueueMessage(text.trim());
      msgInput.value = '';
      return;
    }
    state.isTurnActive = true;

    addMessage('user', text);
    msgInput.value = '';
    setLoading(true);
    const turnId = crypto.randomUUID();
    startStagePolling(turnId);

    try {
      const resp = await api('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: state.userId,
          message: text,
          session_id: state.sessionId,
          turn_id: turnId,
        }),
      });

      state.sessionId = resp.session_id;
      localStorage.setItem('session_id', resp.session_id);

      // Pre-fetch OG previews for inline images — search tasks only
      const citations = resp.citations || [];
      const ogData = {};
      if (resp.task_type === 'search' && citations.length > 0) {
        const toFetch = citations.slice(0, 4);
        await Promise.all(toFetch.map(async (url) => {
          if (state.ogCache[url]) {
            ogData[url] = state.ogCache[url];
          } else {
            try {
              const r = await api(`/og-preview?url=${encodeURIComponent(url)}`);
              if (r.found) {
                state.ogCache[url] = { title: r.title, description: r.description, image: r.image, site_name: r.site_name };
                ogData[url] = state.ogCache[url];
              }
            } catch (_) {}
          }
        }));
      }

      addMessage('assistant', resp.response, {
        task_type: resp.task_type,
        memory_context: resp.memory_context,
        citations: citations,
        ogData: ogData,
        extraction_summary: resp.extraction_summary,
        search_extraction_summary: resp.search_extraction_summary,
      });

      // Voice mode: if TTS is disabled, resume listening manually.
      // If TTS is enabled, speak() will transition back to listening on TTS end.
      if (state.voiceMode && !getSettings().ttsEnabled && !state.isRecording) {
        startListeningForVoice();
      }
    } catch (err) {
      addMessage('assistant', `Error: ${err.message}`);
      if (state.voiceMode && !getSettings().ttsEnabled) {
        startListeningForVoice();
      }
    } finally {
      stopStagePolling();
      setLoading(false);
      state.isTurnActive = false;
      drainMessageQueue();
    }
  }

  // ---- Voice input (MediaRecorder + /transcribe) ----

  async function startRecording() {
    if (state.isRecording) return;
    try {
      // Close any existing audio context first to avoid multiple mic streams
      if (audioContext) {
        await audioContext.close().catch(() => {});
      }

      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      audioContext = new (window.AudioContext || window.webkitAudioContext)();
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      const source = audioContext.createMediaStreamSource(mediaStream);
      source.connect(analyser);

      const mimeType =
        MediaRecorder.isTypeSupported('audio/ogg') ? 'audio/ogg' :
        MediaRecorder.isTypeSupported('audio/wav') ? 'audio/wav' :
        MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' :
        MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4' :
        null;
      if (!mimeType) {
        addMessage('assistant', 'Audio recording not supported in this browser. Try Chrome or Edge.');
        if (state.voiceMode) {
          state.voiceMode = false;
          saveSettings({ voiceMode: false });
          setVoiceState(VoiceState.IDLE, 'audio not supported');
        }
        mediaStream.getTracks().forEach(t => t.stop());
        return;
      }
      console.log('[voice] selected mimeType:', mimeType);
      state.mediaRecorder = new MediaRecorder(mediaStream, { mimeType });
      state.audioChunks = [];
      state.currentMimeType = mimeType;

      state.mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          state.audioChunks.push(e.data);
          console.log('[voice] chunk received:', e.data.size, 'total chunks:', state.audioChunks.length, 'mime:', state.currentMimeType);
        } else {
          console.log('[voice] empty chunk received');
        }
      };

      state.mediaRecorder.onstop = async () => {
        const elapsed = Date.now() - (recordingStartTime || Date.now());
        const currentLoudFrames = loudFrameCount;
        console.log('[voice] onstop chunks:', state.audioChunks.length, 'elapsed:', elapsed, 'loudFrames:', currentLoudFrames);
        try {
          if (state.audioChunks.length === 0) {
            handleRecordingDiscard("Didn't catch that");
            return;
          }
          const tooShort = elapsed < MIN_RECORDING_MS;
          const notEnoughLoud = currentLoudFrames < MIN_AUDIO_FRAMES;
          console.log('[voice] onstop checks tooShort:', tooShort, 'notEnoughLoud:', notEnoughLoud);
          if (tooShort || notEnoughLoud) {
            handleRecordingDiscard("Didn't catch that");
            return;
          }
          const blob = new Blob(state.audioChunks, { type: state.currentMimeType || 'audio/webm' });
          state.audioChunks = [];
          console.log('[voice] sending blob size:', blob.size, 'mime:', state.currentMimeType);
          await sendAudioForTranscription(blob);
        } catch (err) {
          console.error('[voice] onstop error:', err);
        }
      };

      state.mediaRecorder.onerror = (e) => {
        console.error('MediaRecorder error:', e);
        addMessage('assistant', 'Recording failed. Please try again.');
        if (state.voiceMode) scheduleListenRetry('Recording error, retrying...');
      };

      state.mediaRecorder.start();
      state.isRecording = true;
      recordingStartTime = Date.now();
      loudFrameCount = 0;
      silenceAfterLoud = false;

      // Start interval-based audio monitoring (rAF throttles in bg tabs)
      startAudioMonitor();

      // Auto-stop after max duration
      recordingTimeoutId = setTimeout(() => {
        if (state.isRecording) stopRecording();
      }, MAX_RECORDING_MS);
    } catch (e) {
      console.warn('Failed to start recording:', e);
      addMessage('assistant', 'Microphone access denied or not available. Voice mode paused.');
      if (state.voiceMode) {
        state.voiceMode = false;
        saveSettings({ voiceMode: false });
        setVoiceState(VoiceState.IDLE, 'mic permission denied');
      }
    }
  }

  function _checkAudioLevels() {
    if (!analyser) return;
    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(dataArray);
    let sum = 0;
    for (let i = 0; i < dataArray.length; i++) {
      sum += dataArray[i];
    }
    const average = sum / dataArray.length / 255;
    const elapsed = Date.now() - (recordingStartTime || Date.now());
    const metMinDuration = elapsed >= MIN_RECORDING_MS;
    const hasLoudAudio = average >= MIN_AUDIO_LEVEL;
    if (hasLoudAudio) {
      loudFrameCount++;
      silenceAfterLoud = false;
      if (silenceTimeout) {
        clearTimeout(silenceTimeout);
        silenceTimeout = null;
      }
    } else {
      if (!silenceAfterLoud && loudFrameCount > 0) {
        silenceAfterLoud = true;
        console.log('[voice] silence detected after loud audio, will timeout in', SILENCE_DURATION, 'ms');
      }
      if (!silenceTimeout && metMinDuration && loudFrameCount >= MIN_AUDIO_FRAMES) {
        console.log('[voice] setting silence timeout:', SILENCE_DURATION, 'ms, loudFrames:', loudFrameCount);
        silenceTimeout = setTimeout(() => {
          if (state.isRecording) {
            console.log('[voice] silence timeout fired — stopping');
            stopRecording();
          }
        }, SILENCE_DURATION);
      }
    }
  }

  function startAudioMonitor() {
    stopAudioMonitor();
    monitorIntervalId = setInterval(_checkAudioLevels, MONITOR_INTERVAL_MS);
  }

  function stopAudioMonitor() {
    if (monitorIntervalId !== null) {
      clearInterval(monitorIntervalId);
      monitorIntervalId = null;
    }
  }

  let _stopping = false;
  function stopRecording() {
    if (!state.isRecording || !state.mediaRecorder) return;
    if (_stopping) {
      console.log('[voice] stopRecording called while already stopping — ignore');
      return;
    }
    _stopping = true;
    clearTimeout(recordingTimeoutId);
    recordingTimeoutId = null;
    clearTimeout(silenceTimeout);
    silenceTimeout = null;
    stopAudioMonitor();
    const mr = state.mediaRecorder;
    state.mediaRecorder = null;
    state.isRecording = false;
    silenceAfterLoud = false;
    // Reset counters immediately so _checkAudioLevels sees clean state
    // if it fires during the async onstop handler.
    loudFrameCount = 0;
    recordingStartTime = null;
    try {
      mr.stop();
    } catch (e) {
      console.warn('Error stopping recorder:', e);
    }
    if (mediaStream) {
      mediaStream.getTracks().forEach(t => t.stop());
      mediaStream = null;
    }
    if (audioContext) {
      audioContext.close().catch(() => {});
      audioContext = null;
      analyser = null;
    }
    _stopping = false;
  }

  function startBargeInMonitor() {
    stopBargeInMonitor();
    if (!analyser) return;
    const gen = ++bargeInGeneration;
    let consecutiveLoudFrames = 0;
    const requiredFrames = Math.ceil(BARGE_IN_DURATION / 16); // ~60fps

    function tick() {
      if (gen !== bargeInGeneration) return;
      if (voiceState !== VoiceState.SPEAKING || !analyser) return;

      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(dataArray);

      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
      const average = sum / dataArray.length / 255;

      if (average > BARGE_IN_THRESHOLD) {
        consecutiveLoudFrames++;
        if (consecutiveLoudFrames >= requiredFrames) {
          // Barge-in detected: cancel TTS and start listening
          speakGeneration++;
          stopSpeaking();
          stopBargeInMonitor();
          playEarcon('stop');
          startListeningForVoice();
          return;
        }
      } else {
        consecutiveLoudFrames = 0;
      }

      bargeInMonitorId = requestAnimationFrame(tick);
    }

    bargeInMonitorId = requestAnimationFrame(tick);
  }

  function stopBargeInMonitor() {
    if (bargeInMonitorId) {
      cancelAnimationFrame(bargeInMonitorId);
      bargeInMonitorId = null;
    }
  }

  // A recording was discarded (too short / silent). In conversation mode we
  // loop back to listening; in dictation we just surface why and stop.
  function handleRecordingDiscard(message) {
    if (state.dictation) {
      endDictation();
      setVoiceState(VoiceState.IDLE, message);
    } else if (state.voiceMode) {
      scheduleListenRetry(message);
    }
  }

  function scheduleListenRetry(message, delayMs = 1200) {
    setVoiceState(VoiceState.PROCESSING, message);
    setTimeout(() => {
      if (state.voiceMode) {
        startListeningForVoice();
      }
    }, delayMs);
  }

  async function sendAudioForTranscription(blob) {
    console.log('[voice] sendAudioForTranscription blob:', blob.size, 'mime:', blob.type, 'voiceMode:', state.voiceMode, 'dictation:', state.dictation);
    if (!state.voiceMode && !state.dictation) return;
    setVoiceState(VoiceState.PROCESSING, 'transcribing');
    playEarcon('stop');

    try {
      const mimeExt = (state.currentMimeType || 'audio/webm').split('/')[1];
      const formData = new FormData();
      formData.append('file', blob, `audio.${mimeExt}`);

      const res = await fetch(BASE + '/transcribe', {
        method: 'POST',
        body: formData,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Transcription failed' }));
        throw new Error(err.detail || 'Transcription failed');
      }

      const data = await res.json();
      const text = (data.text || '').trim();
      console.log('[voice] transcription response:', text);

      if (!text) {
        handleRecordingDiscard("Didn't catch that");
        return;
      }

      // Dictation: drop the transcript into the input for review — the user
      // decides when to send. Exit commands only apply to conversation mode.
      if (state.dictation) {
        endDictation();
        msgInput.value = msgInput.value ? `${msgInput.value} ${text}` : text;
        msgInput.focus();
        return;
      }

      if (isVoiceExitCommand(text)) {
        exitVoiceMode();
        return;
      }

      sendMessage(text);
    } catch (err) {
      console.error('Transcription error:', err);
      if (state.dictation) {
        endDictation();
        addMessage('assistant', 'Transcription failed. Please try again.');
      } else {
        addMessage('assistant', 'Transcription failed. Please try again.');
        scheduleListenRetry('Transcription error, retrying...');
      }
    }
  }

  // ---- Init ----
  async function init() {
    // Load agent name
    try {
      const { name } = await api('/assistant/name');
      document.getElementById('agent-name').textContent = name;
      if (name) document.title = name;
    } catch (_) {}

    // Load users
    try {
      const users = await api('/users');
      if (users.length === 0) {
        userBadge.textContent = 'No user — create one via CLI';
      } else {
        const savedUserId = localStorage.getItem('user_id');
        const user = savedUserId
          ? users.find(u => u.id === parseInt(savedUserId)) || users[0]
          : users[0];
        state.userId = user.id;
        state.userName = user.name;
        localStorage.setItem('user_id', user.id);
        userBadge.textContent = user.name;
        // Load conversations after user is set
        initConversations();
      }
    } catch (e) {
      userBadge.textContent = 'Error loading user';
    }

    // TTS voices
    if (window.speechSynthesis) {
      speechSynthesis.onvoiceschanged = populateVoices;
      populateVoices();
    }

    // Fetch backend settings ( Brave toggle visibility)
    try {
      const backendSettings = await api('/settings');
      state.braveConfigured = backendSettings.brave_configured;
      state.braveEnabled = backendSettings.brave_enabled;
      if (state.braveConfigured) {
        document.getElementById('search-settings-section').style.display = 'block';
        document.getElementById('brave-enabled').checked = state.braveEnabled;
      }
    } catch (_) {}

    // Apply settings
    applySettings();

    // Restore voice mode if it was active before reload
    if (getSettings().voiceMode && state.userId) {
      enterVoiceMode();
    }

    // Restore the previous conversation so a refresh or a detour to the
    // brain page doesn't lose the visible thread. session_id already persists
    // in localStorage (memory continuity); this restores what was said.
    // Only restore if a user is logged in (state.userId is set).
    if (state.sessionId && state.userId) {
      try {
        const history = await api(
          `/chat/session/${encodeURIComponent(state.sessionId)}/messages?user_id=${state.userId}&limit=50`
        );
        for (const m of history) {
          if (m.role === 'user' || m.role === 'assistant') addMessage(m.role, m.content);
        }
        if (history.length) messagesEl.scrollTop = messagesEl.scrollHeight;
      } catch (_) { /* history restore is best-effort */ }
    } else if (!state.userId) {
      // No user logged in — prompt to create one. Conversation restore
      // is scoped per-user, so it can't happen without a user identity.
      userBadge.textContent = 'No user — create one via CLI or use API';
    }

    // Events
    sendBtn.addEventListener('click', () => sendMessage(msgInput.value));
    msgInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage(msgInput.value);
      }
    });

    micBtn.addEventListener('click', () => {
      if (state.isRecording) {
        stopRecording();
      } else if (state.voiceMode) {
        // Voice mode is active; force a fresh listen cycle
        startListeningForVoice();
      } else {
        startDictation();
      }
    });

    voiceModeBtn.addEventListener('click', () => {
      if (state.voiceMode) {
        exitVoiceMode();
      } else {
        enterVoiceMode();
      }
    });

    stopSpeakingBtn.addEventListener('click', () => {
      stopSpeaking();
      if (state.voiceMode && voiceState === VoiceState.SPEAKING) {
        startListeningForVoice();
      }
    });

    voiceStopBtn.addEventListener('click', () => {
      if (state.dictation) {
        stopRecording();
        return;
      }
      // In voice mode: stop TTS and immediately start listening (barge-in),
      // without fully exiting voice mode. Exit is only for explicit cancel.
      if (state.voiceMode) {
        speakGeneration++;   // invalidate onend callbacks
        stopSpeaking();
        stopBargeInMonitor();
        playEarcon('stop');
        startListeningForVoice();
        return;
      }
      // Not in voice mode: stop TTS only
      speakGeneration++;
      stopSpeaking();
    });
    voiceCancelBtn.addEventListener('click', () => {
      if (state.dictation) {
        state.isRecording = false;
        clearTimeout(recordingTimeoutId);
        if (silenceTimeout) { clearTimeout(silenceTimeout); silenceTimeout = null; }
        try { state.mediaRecorder?.stop(); } catch (_) {}
        endDictation();
      } else {
        exitVoiceMode();
      }
    });

    // Inline voice status bar buttons — same actions as overlay buttons
    voiceStopInline.addEventListener('click', () => voiceStopBtn.click());
    voiceCancelInline.addEventListener('click', () => voiceCancelBtn.click());

    settingsToggle.addEventListener('click', () => {
      settingsPanel.classList.toggle('open');
    });

    settingsClose.addEventListener('click', () => {
      settingsPanel.classList.remove('open');
    });

    ttsEnabled.addEventListener('change', () => {
      saveSettings({ ttsEnabled: ttsEnabled.checked });
    });

    voiceSelect.addEventListener('change', () => {
      saveSettings({ voiceUri: voiceSelect.value });
    });

    // Voice speed, pitch, volume controls
    const voiceSpeed = $('#voice-speed');
    const voicePitch = $('#voice-pitch');
    const voiceVolume = $('#voice-volume');
    const speedValue = $('#voice-speed-value');
    const pitchValue = $('#voice-pitch-value');
    const volumeValue = $('#voice-volume-value');

    // Update displayed values
    voiceSpeed.addEventListener('input', () => {
      speedValue.textContent = `${voiceSpeed.value}x`;
    });
    
    voicePitch.addEventListener('input', () => {
      pitchValue.textContent = `${voicePitch.value}x`;
    });
    
    voiceVolume.addEventListener('input', () => {
      volumeValue.textContent = voiceVolume.value;
    });

    // Save settings when controls change
    voiceSpeed.addEventListener('change', () => {
      saveSettings({ voiceSpeed: parseFloat(voiceSpeed.value) });
    });
    
    voicePitch.addEventListener('change', () => {
      saveSettings({ voicePitch: parseFloat(voicePitch.value) });
    });
    
    voiceVolume.addEventListener('change', () => {
      saveSettings({ voiceVolume: parseFloat(voiceVolume.value) });
    });

    traceVisible.addEventListener('change', () => {
      saveSettings({ traceVisible: traceVisible.checked });
    });

    $('#trace-close').addEventListener('click', () => {
      saveSettings({ traceVisible: false });
});

// File attachment state
    let attachedFiles = [];

    // Initialize file attachment UI
    initFileAttach();

    function initFileAttach() {
      const fileAttachBtn = $('#file-attach-btn');
      const fileInput = $('#file-input');
      const fileChipsPreview = $('#file-chips-preview');

      // Click button to open file picker
      fileAttachBtn.addEventListener('click', () => {
        fileInput.click();
      });

      // Handle file selection
      fileInput.addEventListener('change', handleFileSelect);

      // Drag-and-drop support
      const inputRow = $('#input-row');
      inputRow.addEventListener('dragover', (e) => {
        e.preventDefault();
        inputRow.style.borderColor = 'var(--accent)';
      });

      inputRow.addEventListener('dragleave', (e) => {
        e.preventDefault();
        inputRow.style.borderColor = 'var(--border)';
      });

      inputRow.addEventListener('drop', (e) => {
        e.preventDefault();
        inputRow.style.borderColor = 'var(--border)';
        const files = e.dataTransfer.files;
        if (files.length > 0) {
          handleFiles(files);
        }
      });

      function handleFileSelect(e) {
        handleFiles(e.target.files);
      }

      function handleFiles(files) {
        const validTypes = ['.txt', '.csv', '.json', '.xml', '.html', '.ics'];
        const maxSize = 10 * 1024 * 1024; // 10MB

        for (const file of files) {
          const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
          if (!validTypes.includes(ext)) {
            showToast(`Unsupported file type: ${ext}`, 'error');
            continue;
          }

          if (file.size > maxSize) {
            showToast(`File too large: ${formatBytes(file.size)} (max 10MB)`, 'error');
            continue;
          }

          // Check for duplicates
          const exists = attachedFiles.some(f => f.name === file.name && f.size === file.size);
          if (exists) {
            showToast(`File already attached: ${file.name}`, 'warning');
            continue;
          }

          // Read file content as text
          const reader = new FileReader();
          reader.onload = (e) => {
            const content = e.target.result;
            attachedFiles.push({
              name: file.name,
              size: file.size,
              type: ext,
              content: content,
              element: null
            });
            renderFileChips();
            updateSendButtonState();
          };
          reader.onerror = () => {
            showToast(`Error reading file: ${file.name}`, 'error');
          };
          reader.readAsText(file);
        }
      }

      function renderFileChips() {
        const chipsContainer = $('#file-chips-preview');
        chipsContainer.innerHTML = '';

        attachedFiles.forEach((file, index) => {
          const chip = document.createElement('div');
          chip.className = 'file-chip';
          chip.innerHTML = `
            <span class="file-icon">📄</span>
            <span>${file.name}</span>
            <button class="remove" data-index="${index}" title="Remove">×</button>
          `;
          chip.querySelector('.remove').addEventListener('click', (e) => {
            e.stopPropagation();
            removeFile(index);
          });
          chipsContainer.appendChild(chip);
        });
      }

      function removeFile(index) {
        attachedFiles.splice(index, 1);
        renderFileChips();
        updateSendButtonState();
      }

      function updateSendButtonState() {
        const sendBtn = $('#send-btn');
        if (attachedFiles.length > 0) {
          sendBtn.disabled = false;
          // Can add a badge showing attached file count
        } else {
          sendBtn.disabled = true;
        }
      }
    }

    // Handle form submission with files
    const originalSend = async () => {
      // ... existing send logic
    };

    // Override send to include files
    const sendForm = async () => {
      const text = msgInput.value.trim();
      if (!text && attachedFiles.length === 0) return;

      // Show attached files in chat thread first
      if (attachedFiles.length > 0) {
        attachedFiles.forEach(file => {
          appendFileMessage(file);
        });
      }

      // Show queued state
      const sendBtn = $('#send-btn');
      const queuedDiv = document.createElement('div');
      queuedDiv.className = 'msg-queued';
      queuedDiv.innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20 6L9 17l-5-5"/>
        </svg>
        Sending...
      `;
      sendBtn.parentNode.appendChild(queuedDiv);
      sendBtn.disabled = true;

      try {
        // Build JSON body with attached files
        const body = {
          user_id: 1,
          message: text,
          attached_files: attachedFiles.map(f => ({
            name: f.name,
            ext: f.type,
            preview: f.content.slice(0, 500),
            content: f.content,
            text: f.content,
            key_entities: [],
            open_questions: []
          }))
        };

        // Send via fetch
        const response = await fetch('/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
          credentials: 'include'
        });

        const data = await response.json();

        if (response.ok) {
          // Append assistant message
          appendAssistantMessage(data.response || '');

          // Clear input
          msgInput.value = '';
          attachedFiles = [];
          renderFileChips();
          updateSendButtonState();
          msgInput.focus();
        } else {
          showToast(data.detail || 'Error sending message', 'error');
        }
      } catch (err) {
        showToast('Error sending message: ' + err.message, 'error');
      } finally {
        // Remove queued state
        queuedDiv.remove();
        sendBtn.disabled = attachedFiles.length > 0 ? false : true;
      }
    };

    $('#send-btn').addEventListener('click', sendForm);

    // Allow sending with Enter+Shift for multi-line
    msgInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendForm();
      }
    });
  }

  init();
})();
