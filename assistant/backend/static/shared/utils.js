/**
 * Shared utilities for chat.html and brain.html
 * ES Module - no build step required
 */

// DOM query helpers
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => root.querySelectorAll(sel);

// API wrapper with credentials
export const api = (path, opts = {}) => {
  return fetch(window.location.origin + path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts
  }).then(r => {
    if (!r.ok) throw new Error(r.statusText);
    return r.json().catch(() => ({ detail: r.statusText }));
  });
};

// Scroll to bottom of messages container
export const scrollBottom = () => {
  const el = $('#messages');
  if (el) el.scrollTop = el.scrollHeight;
};

// Settings management (localStorage)
export const getSettings = () => {
  try {
    return JSON.parse(localStorage.getItem('assistant_settings') || '{}');
  } catch {
    return {};
  }
};

export const saveSettings = (s) => {
  localStorage.setItem('assistant_settings', JSON.stringify(s));
};

export const applySettings = () => {
  const s = getSettings();
  document.documentElement.style.setProperty('--font', s.font || '');
  // Apply other settings as needed
};

// Format bytes for display
export const formatBytes = (bytes) => {
  if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  if (bytes >= 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return bytes + ' B';
};

// Simple toast notification
export const showToast = (message, type = 'info') => {
  const existing = document.querySelector('.toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  toast.style.cssText = `
    position: fixed; bottom: 2rem; left: 50%; transform: translateX(-50%);
    background: var(--surface); border: 1px solid ${type === 'error' ? 'var(--error)' : 'var(--accent)'};
    color: var(--text); padding: 0.75rem 1.25rem; border-radius: 6px; z-index: 1000;
    animation: fadeIn 0.3s ease-out;
  `;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
};

// Add toast animation if not exists
if (!document.getElementById('toast-styles')) {
  const style = document.createElement('style');
  style.id = 'toast-styles';
  style.textContent = `
    @keyframes fadeIn { from { opacity: 0; transform: translateX(-50%) translateY(10px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
  `;
  document.head.appendChild(style);
}
