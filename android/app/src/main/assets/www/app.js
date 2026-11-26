'use strict';

const byId = (id) => document.getElementById(id);
const preferences = {
  get(key) {
    try { return localStorage.getItem('campbuddy_' + key); } catch { return null; }
  },
  set(key, value) {
    try { localStorage.setItem('campbuddy_' + key, value); } catch { /* Storage is optional. */ }
  }
};
const android = location.hostname === 'appassets.androidplatform.net';
const defaultApi = android ? 'http://10.0.2.2:8000' :
  /^https?:$/.test(location.protocol) ? location.origin : 'http://localhost:8000';

function backendUrl(value) {
  try {
    const url = new URL(value);
    if (/^https?:$/.test(url.protocol) && !url.username && !url.password && !url.search && !url.hash) {
      return url.href.replace(/\/+$/, '');
    }
  } catch { /* The settings form displays the validation error. */ }
  return '';
}

function validMessages(items) {
  return Array.isArray(items) ? items.filter((item) => item && ['user', 'assistant'].includes(item.role) && typeof item.content === 'string') : [];
}

function readConversations() {
  try {
    const saved = JSON.parse(preferences.get('conversations') || 'null');
    if (Array.isArray(saved?.threads)) {
      const threads = saved.threads
        .filter((thread) => typeof thread?.id === 'string')
        .map((thread) => ({ id: thread.id, messages: validMessages(thread.messages) }))
        .filter((thread) => thread.messages.length);
      return { threads, activeId: saved.activeId };
    }
    const messages = validMessages(JSON.parse(preferences.get('chat_history') || '[]'));
    if (messages.length) return { threads: [{ id: 'previous', messages }], activeId: 'previous' };
  } catch { /* Start with an empty conversation if saved data is invalid. */ }
  return { threads: [], activeId: null };
}

const saved = readConversations();
const state = {
  api: backendUrl(preferences.get('api_url')) || defaultApi,
  topK: Math.min(10, Math.max(1, parseInt(preferences.get('top_k'), 10) || 3)),
  threads: saved.threads,
  active: saved.threads.find((thread) => thread.id === saved.activeId) || null,
  launch: 0,
  chat: null,
  mutation: false,
  canClear: false,
  health: null
};

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text != null) element.textContent = text;
  if (className) element.className = className;
  return element;
}

async function api(path, options = {}, base = state.api) {
  const signal = options.signal || AbortSignal.timeout?.(120000);
  let response;
  try {
    response = await fetch(base + path, { ...options, signal, cache: 'no-store' });
  } catch (error) {
    if (signal?.aborted && signal.reason?.name === 'TimeoutError') {
      throw new Error('The request timed out. Please try again.');
    }
    if (error.name === 'AbortError') throw error;
    throw new Error('Could not reach the backend. Check the API URL in Settings.');
  }
  const data = await response.json().catch(() => null);
  if (!response.ok || !data) {
    throw new Error(typeof data?.detail === 'string' ? data.detail : 'Unexpected response from the backend (' + response.status + ').');
  }
  return data;
}

function json(body) {
  return { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
}

function updateIndexControls() {
  byId('open-clear').disabled = state.mutation || !state.canClear;
  byId('drop-zone').disabled = state.mutation;
}

function renderDocuments(documents, chunks) {
  byId('document-count').textContent = documents.length;
  byId('chunks').textContent = chunks + (chunks === 1 ? ' chunk indexed' : ' chunks indexed');
  const cards = documents.map((filename) => {
    const row = node('div', null, 'document');
    row.append(node('span', filename.split('.').pop().toUpperCase(), 'file-type'), node('span', filename));
    return row;
  });
  byId('documents').replaceChildren(...(cards.length ? cards : [node('p', 'Upload a campus document to get started.', 'muted')]));
}

async function refreshLibrary() {
  const launch = ++state.launch;
  const base = state.api;
  const signal = AbortSignal.timeout?.(10000);
  const [health, library] = await Promise.allSettled([api('/health', { signal }, base), api('/documents', { signal }, base)]);
  if (launch !== state.launch) return;

  state.health = health.status === 'fulfilled' ? health.value : null;
  const badge = byId('connection');
  badge.dataset.state = state.health ? 'online' : 'offline';
  badge.textContent = state.health?.mode === 'local_preview' ? 'Local document mode' : state.health ? 'API connected' : 'API offline';
  badge.title = badge.textContent;
  byId('api-status').textContent = state.health?.mode === 'local_preview'
    ? 'Local document mode shows matching excerpts from your files.'
    : state.health ? (state.health.gemini_configured ? 'Gemini is configured on this backend.' : 'Gemini needs a server-side API key.')
    : 'Start the backend or check this URL.';
  const docs = library.status === 'fulfilled' ? library.value.documents : state.health?.indexed_documents;
  if (Array.isArray(docs)) {
    const chunks = (library.status === 'fulfilled' ? library.value.total_chunks : state.health?.total_indexed_chunks) ?? 0;
    renderDocuments(docs, chunks);
    state.canClear = chunks > 0 || docs.length > 0;
  } else {
    byId('document-count').textContent = '–';
    byId('chunks').textContent = '– chunks';
    byId('documents').replaceChildren(node('p', 'Library unavailable. Check the backend in Settings.', 'muted'));
    state.canClear = false;
  }
  updateIndexControls();
}

function uploadStatus(message, status) {
  const element = byId('upload-status');
  element.textContent = message;
  element.dataset.state = status;
  element.hidden = !message;
}

async function upload(file) {
  if (!file || state.mutation) return;
  if (!/\.(pdf|docx|txt)$/i.test(file.name)) {
    uploadStatus('Choose a PDF, DOCX, or TXT file.', 'error');
    return;
  }
  state.mutation = true;
  updateIndexControls();
  uploadStatus('Indexing ' + file.name + '…', 'pending');
  const form = new FormData();
  form.append('file', file);
  try {
    const data = await api('/upload', { method: 'POST', body: form });
    if (data.status !== 'success') throw new Error(data.message || 'The document could not be processed.');
    if (data.embedding_info?.status === 'error') {
      throw new Error(data.embedding_info.message || 'Could not index the extracted text.');
    }
    uploadStatus('Indexed ' + file.name + ' · ' + data.total_chunks + ' passages', 'success');
  } catch (error) {
    uploadStatus(error.message, 'error');
  } finally {
    await refreshLibrary();
    state.mutation = false;
    updateIndexControls();
  }
}

function sourceCard(source, withText = false) {
  const card = node('div', null, withText ? 'result' : 'source');
  card.append(node('strong', source.filename || 'Campus document'));
  const metadata = [];
  if (source.page_number != null) metadata.push('Page ' + source.page_number);
  if (typeof source.similarity_score === 'number') metadata.push(Math.round(source.similarity_score * 100) + '% similarity');
  if (metadata.length) card.append(node('span', metadata.join(' · ')));
  if (withText) card.append(node('p', source.text || ''));
  return card;
}

// Render the headings, lists and inline emphasis commonly returned by Gemini.
// All model text stays in text nodes, including HTML and link syntax.
function inlineMarkdown(parent, text) {
  const tokens = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`)/g;
  let offset = 0;
  for (const match of text.matchAll(tokens)) {
    parent.append(text.slice(offset, match.index));
    parent.append(node(match[2] ? 'strong' : match[3] ? 'em' : 'code', match[2] || match[3] || match[4]));
    offset = match.index + match[0].length;
  }
  parent.append(text.slice(offset));
}

function renderAnswer(parent, content) {
  let list;
  let paragraph;
  for (const line of content.split(/\r?\n/)) {
    const bullet = /^\s*([-*]|\d+\.)\s+(.+)$/.exec(line);
    const heading = /^#{1,6}\s+(.+)$/.exec(line);
    if (bullet) {
      paragraph = null;
      const tag = /\d/.test(bullet[1]) ? 'OL' : 'UL';
      if (list?.tagName !== tag) {
        list = node(tag.toLowerCase());
        parent.append(list);
      }
      const item = node('li');
      inlineMarkdown(item, bullet[2]);
      list.append(item);
    } else {
      list = null;
      if (heading) {
        paragraph = null;
        const title = node('h3');
        inlineMarkdown(title, heading[1]);
        parent.append(title);
      } else if (line.trim()) {
        if (!paragraph) {
          paragraph = node('p');
          parent.append(paragraph);
        } else {
          paragraph.append('\n');
        }
        inlineMarkdown(paragraph, line);
      } else {
        paragraph = null;
      }
    }
  }
}

function renderMessage(message) {
  const user = message.role === 'user';
  const card = node('article', null, 'message' + (user ? ' user' : ''));
  const body = node('div', null, 'message-body');
  card.append(node('span', user ? 'You' : 'CampBuddy', 'message-label'), body);
  if (user) body.textContent = message.content;
  else renderAnswer(body, message.content);
  if (Array.isArray(message.sources) && message.sources.length) {
    const sources = node('details', null, 'sources');
    sources.open = true;
    sources.append(node('summary', 'Sources (' + message.sources.length + ')'), ...message.sources.filter(Boolean).map((source) => sourceCard(source)));
    card.append(sources);
  }
  byId('messages').append(card);
  return card;
}

function conversationTitle(thread) {
  return thread?.messages.find((message) => message.role === 'user')?.content.replace(/\s+/g, ' ') || 'Campus assistant';
}

function saveConversations() {
  const threads = state.threads.map(({ id, messages }) => ({
    id, messages: messages.length <= 30 ? messages : messages.slice(0, 1).concat(messages.slice(-29))
  }));
  preferences.set('conversations', JSON.stringify({ threads, activeId: state.active?.id || null }));
}

function renderRecent() {
  const items = state.threads.map((thread) => {
    const title = conversationTitle(thread);
    const row = node('div', null, 'recent-item');
    const open = node('button', title);
    open.type = 'button';
    open.title = title;
    if (thread === state.active) open.setAttribute('aria-current', 'true');
    open.addEventListener('click', () => openConversation(thread));
    const remove = node('button', '×', 'remove-chat');
    remove.type = 'button';
    remove.setAttribute('aria-label', 'Delete chat: ' + title);
    remove.addEventListener('click', () => {
      if (!confirm('Delete this saved conversation?')) return;
      state.threads = state.threads.filter((item) => item !== thread);
      if (state.active === thread) openConversation(null);
      else {
        saveConversations();
        renderRecent();
      }
    });
    row.append(open, remove);
    return row;
  });
  byId('recent-chats').replaceChildren(...(items.length ? items : [node('p', 'Your conversations will appear here.', 'muted')]));
}

function updateChat() {
  const hasHistory = Boolean(state.active?.messages.length);
  byId('welcome').hidden = hasHistory;
  byId('suggestions').hidden = hasHistory;
  byId('main').classList.toggle('empty', !hasHistory);
  byId('conversation-title').textContent = conversationTitle(state.active);
  byId('send').hidden = Boolean(state.chat);
  byId('stop').hidden = !state.chat;
  byId('send').disabled = !byId('question').value.trim();
  byId('question').style.height = 'auto';
  byId('question').style.height = Math.min(byId('question').scrollHeight, 128) + 'px';
}

function scrollChat() {
  const scroll = byId('chat-scroll');
  scroll.scrollTop = scroll.scrollHeight;
}

function addMessage(role, content, sources) {
  if (!state.active) {
    state.active = { id: Date.now().toString(36) + Math.random().toString(36).slice(2), messages: [] };
    state.threads.unshift(state.active);
  }
  const message = { role, content, sources, timestamp: new Date().toISOString() };
  state.active.messages.push(message);
  saveConversations();
  renderMessage(message);
  renderRecent();
  scrollChat();
}

function stopResponse() {
  if (!state.chat) return;
  state.chat.abort();
  state.chat = null;
  state.pending?.remove();
  state.pending = null;
  updateChat();
}

function openConversation(thread) {
  setAlarmView(false);
  if (thread !== state.active) {
    stopResponse();
    state.active = thread;
    byId('messages').replaceChildren();
    thread?.messages.forEach(renderMessage);
    question.value = '';
    saveConversations();
    renderRecent();
    updateChat();
    scrollChat();
  }
  if (mobile.matches) sidebar(false);
  question.focus();
}

async function submitQuestion(question) {
  if (!question || state.chat) return;
  const base = state.api;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(new DOMException('Request timed out', 'TimeoutError')), 120000);
  state.chat = controller;
  byId('question').value = '';
  addMessage('user', question);
  updateChat();
  const pending = renderMessage({ role: 'assistant', content: 'Searching your campus documents…' });
  state.pending = pending;
  scrollChat();
  try {
    const data = await api('/chat', { ...json({ question, top_k: state.topK }), signal: controller.signal }, base);
    if (state.chat === controller) {
      pending.remove();
      recordKnowledgeOutcome(base, question, data.status);
      addMessage('assistant', data.answer || 'The backend returned an empty answer.', data.sources || []);
    }
  } catch (error) {
    if (state.chat === controller) {
      pending.remove();
      addMessage('assistant', error.message);
    }
  } finally {
    clearTimeout(timeout);
    if (state.chat === controller) {
      state.chat = null;
      state.pending = null;
      updateChat();
    }
  }
}

const question = byId('question');
question.addEventListener('input', updateChat);
question.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submitQuestion(question.value.trim());
  }
});
byId('chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  submitQuestion(question.value.trim());
});
document.querySelectorAll('[data-prompt]').forEach((button) => {
  button.addEventListener('click', () => {
    question.focus();
    submitQuestion(button.dataset.prompt);
  });
});
byId('new-chat').addEventListener('click', () => {
  question.value = '';
  openConversation(null);
  updateChat();
});
byId('stop').addEventListener('click', () => {
  stopResponse();
  question.focus();
});

const libraryDialog = byId('library');
function openLibrary() {
  if (mobile.matches) sidebar(false);
  libraryDialog.showModal();
}
byId('open-library').addEventListener('click', openLibrary);
byId('attach').addEventListener('click', openLibrary);

const file = byId('file');
const dropZone = byId('drop-zone');
dropZone.addEventListener('click', () => file.click());
file.addEventListener('change', () => {
  const selected = file.files[0];
  file.value = '';
  upload(selected);
});
dropZone.addEventListener('dragover', (event) => {
  event.preventDefault();
  if (!state.mutation) dropZone.classList.add('dragging');
});
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragging'));
dropZone.addEventListener('drop', (event) => {
  event.preventDefault();
  dropZone.classList.remove('dragging');
  upload(event.dataTransfer.files[0]);
});

document.querySelectorAll('[data-close]').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog').close());
});

byId('open-clear').addEventListener('click', () => byId('clear').showModal());
byId('clear-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.mutation || !state.canClear) return;
  byId('clear').close();
  state.mutation = true;
  updateIndexControls();
  uploadStatus('Clearing the document index…', 'pending');
  try {
    await api('/clear', { method: 'POST' });
    uploadStatus('Document index cleared.', 'success');
  } catch (error) {
    uploadStatus(error.message, 'error');
  } finally {
    await refreshLibrary();
    state.mutation = false;
    updateIndexControls();
  }
});

const apiInput = byId('api-url');
const topK = byId('top-k');
byId('open-settings').addEventListener('click', () => {
  if (mobile.matches) sidebar(false);
  apiInput.value = state.api;
  apiInput.setCustomValidity('');
  topK.value = state.topK;
  byId('top-k-value').value = state.topK;
  byId('settings').showModal();
});
apiInput.addEventListener('input', () => apiInput.setCustomValidity(''));
topK.addEventListener('input', () => { byId('top-k-value').value = topK.value; });
byId('settings-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const base = backendUrl(apiInput.value.trim());
  if (!base || (state.mutation && base !== state.api)) {
    apiInput.setCustomValidity(base ? 'Wait for the current library update before switching servers.' : 'Enter an HTTP or HTTPS backend URL.');
    apiInput.reportValidity();
    if (base) apiInput.setCustomValidity('');
    return;
  }
  if (state.api !== base) {
    state.canClear = false;
    updateIndexControls();
    uploadStatus('', '');
    byId('connection').dataset.state = 'pending';
    byId('connection').textContent = 'Connecting';
    byId('documents').replaceChildren(node('p', 'Loading documents…', 'muted'));
    byId('document-count').textContent = '–';
    byId('chunks').textContent = '– chunks';
    byId('search-results').replaceChildren();
  }
  state.api = base;
  state.topK = Number(topK.value);
  preferences.set('api_url', base);
  preferences.set('top_k', state.topK);
  byId('settings').close();
  if (!byId('alerts').hidden) refreshAlarmDashboard();
  refreshLibrary();
});

const search = byId('search');
const searchQuery = byId('search-query');
const results = byId('search-results');
const searchButton = byId('run-search');
byId('open-search').addEventListener('click', () => {
  if (mobile.matches) sidebar(false);
  search.showModal();
  searchQuery.focus();
});
byId('search-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const query = searchQuery.value.trim();
  if (!query || searchButton.disabled) return;
  const base = state.api;
  searchButton.disabled = true;
  results.replaceChildren(node('p', 'Searching the document index…', 'muted'));
  try {
    const data = await api('/search', json({ query, top_k: 5 }), base);
    if (base !== state.api) return;
    const matches = (data.results || []).map((item) => sourceCard(item, true));
    results.replaceChildren(...(matches.length ? matches : [node('p', 'No matching passages. Check that documents are indexed.', 'muted')]));
  } catch (error) {
    if (base === state.api) results.replaceChildren(node('p', error.message, 'danger'));
  } finally {
    searchButton.disabled = false;
  }
});

function theme(dark) {
  document.documentElement.classList.toggle('dark', dark);
  byId('theme').setAttribute('aria-pressed', String(dark));
  byId('theme').textContent = dark ? '☀' : '☾';
  document.querySelector('meta[name="theme-color"]').content = dark ? '#21151b' : '#fff8fa';
}
const storedTheme = preferences.get('theme');
theme(storedTheme ? storedTheme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches);
byId('theme').addEventListener('click', () => {
  const dark = !document.documentElement.classList.contains('dark');
  theme(dark);
  preferences.set('theme', dark ? 'dark' : 'light');
});

const mobile = matchMedia('(max-width: 760px)');
let desktopSidebar = true;
function sidebar(open) {
  if (mobile.matches) {
    byId('sidebar').classList.toggle('open', open);
    byId('scrim').hidden = !open;
  } else {
    desktopSidebar = open;
    byId('workspace').classList.toggle('collapsed', !open);
  }
  byId('menu').setAttribute('aria-expanded', String(open));
  byId('alarm-menu').setAttribute('aria-expanded', String(open));
}
byId('menu').addEventListener('click', () => sidebar(mobile.matches ? byId('scrim').hidden : !desktopSidebar));
byId('scrim').addEventListener('click', () => { sidebar(false); byId('menu').focus(); });
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !byId('scrim').hidden && !document.querySelector('dialog[open]')) {
    sidebar(false);
    byId('menu').focus();
  }
});
mobile.addEventListener('change', () => {
  byId('sidebar').classList.remove('open');
  byId('scrim').hidden = true;
  sidebar(mobile.matches ? false : desktopSidebar);
});
sidebar(!mobile.matches);

state.active?.messages.forEach(renderMessage);
renderRecent();
updateChat();
scrollChat();
refreshLibrary();
window.addEventListener('online', refreshLibrary);
if (!android && 'serviceWorker' in navigator && window.isSecureContext) {
  navigator.serviceWorker.register('sw.js').catch(() => {});
}
