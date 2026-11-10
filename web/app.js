const $ = (selector) => document.querySelector(selector);
const historyEl = $('#history');
let activeId = localStorage.getItem('apTutorQuestionId');
let learningMode = true;
let busy = false;
const hintNames = ['問題の理解', '着眼点', '身近な例', '基礎知識', '解法に近いヒント', '正答と理由'];

async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = `エラー (${response.status})`;
    try { message = (await response.json()).detail || message; } catch {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const element = $('#toast');
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove('show'), 2800);
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function setMode(enabled) {
  learningMode = enabled;
  $('#mode-label').textContent = `学習モード ${enabled ? 'ON' : 'OFF'}`;
  $('#mode-toggle').classList.toggle('off', !enabled);
}

async function loadHistory() {
  const questions = await api('/api/questions');
  historyEl.innerHTML = questions.map((q) => `
    <button class="history-item ${q.id === activeId ? 'active' : ''}" data-id="${esc(q.id)}">
      <img class="history-thumb" src="/api/questions/${encodeURIComponent(q.id)}/image" alt="">
      <span class="history-meta"><strong>${esc((q.recognized_text || '新しい問題').split('\n')[0].slice(0, 30))}</strong><small>${esc(q.subject ? `科目${q.subject} · ` : '')}${new Date(q.created_at).toLocaleDateString('ja-JP')}</small></span>
    </button>`).join('');
  historyEl.querySelectorAll('.history-item').forEach((button) => button.addEventListener('click', () => selectQuestion(button.dataset.id)));
}

function addMessage(role, content, options = {}) {
  const message = document.createElement('div');
  message.className = `message ${role}${options.typing ? ' typing' : ''}`;
  message.textContent = content;
  $('#messages').append(message);
  $('#messages').scrollTop = $('#messages').scrollHeight;
  return message;
}

function updateHint(level) {
  $('#hint-level').innerHTML = Array.from({length: 5}, (_, i) => `<i class="hint-pip ${i < level ? 'active' : ''}"></i>`).join('');
  $('#hint-label').textContent = hintNames[level] || hintNames[0];
  $('#more-hint').disabled = level >= 4;
}

async function selectQuestion(id) {
  activeId = id;
  localStorage.setItem('apTutorQuestionId', id);
  const [question, messages] = await Promise.all([
    api(`/api/questions/${encodeURIComponent(id)}`), api(`/api/questions/${encodeURIComponent(id)}/messages`),
  ]);
  $('#empty-state').classList.add('hidden');
  $('#study-view').classList.remove('hidden');
  const image = $('#question-image');
  if (question.image_available) {
    image.src = question.image_url;
    image.hidden = false;
    $('#image-placeholder').classList.add('hidden');
  } else {
    image.hidden = true;
    $('#image-placeholder').classList.remove('hidden');
    $('#image-placeholder').querySelector('p').textContent = '画像は一時保持されていません。再撮影してください。';
  }
  $('#question-date').textContent = new Date(question.created_at).toLocaleString('ja-JP');
  $('#subject-select').value = question.subject || '';
  $('#subject-label').textContent = question.subject ? `· 科目${question.subject}` : '';
  $('#recognized-text').textContent = question.recognized_text || '画像を読み取っています…';
  $('#messages').replaceChildren();
  messages.forEach((message) => addMessage(message.role, message.content));
  updateHint(question.hint_level);
  $('#answer-banner').classList.toggle('hidden', !question.reveal_answer);
  $('#reveal-answer').textContent = question.reveal_answer ? '正答開示を許可済み' : '正答を確認';
  await loadHistory();
}

async function sendMessage(text) {
  const message = (text ?? $('#chat-input').value).trim();
  if (!message || !activeId || busy) return;
  if (!learningMode) return toast('学習モードをONにしてください');
  busy = true;
  $('#send-button').disabled = true;
  addMessage('user', message);
  $('#chat-input').value = '';
  const loading = addMessage('assistant', '考えを整理しています…', {typing: true});
  try {
    const result = await api(`/api/questions/${encodeURIComponent(activeId)}/chat`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message}),
    });
    loading.remove();
    addMessage('assistant', result.content);
  } catch (error) {
    loading.remove();
    addMessage('system', error.message);
  } finally {
    busy = false;
    $('#send-button').disabled = false;
  }
}

async function handleUploadResponse(event) {
  if (event.type === 'question_created') {
    await loadHistory();
    await selectQuestion(event.question.id);
    toast('撮影した問題が届きました。読み取っています…');
  } else if (event.type === 'analysis_complete' && event.id === activeId) {
    $('#recognized-text').textContent = event.text;
    const messages = await api(`/api/questions/${encodeURIComponent(activeId)}/messages`);
    $('#messages').replaceChildren();
    messages.forEach((message) => addMessage(message.role, message.content));
    await loadHistory();
  } else if (event.type === 'question_deleted' && event.id === activeId) {
    activeId = null;
    localStorage.removeItem('apTutorQuestionId');
    showEmpty();
    await loadHistory();
  } else if (event.type === 'settings_changed') setMode(event.learning_mode);
}

function connectSocket() {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${scheme}//${location.host}/ws`);
  socket.onopen = () => { $('#connection').innerHTML = '<i></i> 接続中'; };
  socket.onclose = () => { $('#connection').textContent = '再接続中'; setTimeout(connectSocket, 1800); };
  socket.onmessage = async (event) => {
    try { await handleUploadResponse(JSON.parse(event.data)); } catch (error) { console.error(error); }
  };
}

async function updateAgentStatus() {
  const badge = $('#agent-connection');
  try {
    const status = await api('/api/agent/status');
    badge.textContent = !status.online ? '撮影連携が未起動' :
      status.clipboard_available ? 'Fn + F6 連携中' : '画像クリップボード未設定';
    badge.classList.toggle('online', status.online && status.clipboard_available);
  } catch {
    badge.textContent = '撮影連携を確認できません';
    badge.classList.remove('online');
  }
}

function showEmpty() {
  activeId = null;
  $('#empty-state').classList.remove('hidden');
  $('#study-view').classList.add('hidden');
}

$('#new-question').addEventListener('click', showEmpty);
$('#chat-form').addEventListener('submit', (event) => { event.preventDefault(); sendMessage(); });
$('#chat-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage(); }
});
document.querySelectorAll('.quick-actions button').forEach((button) => button.addEventListener('click', () => sendMessage(button.dataset.prompt)));

$('#mode-toggle').addEventListener('click', async () => {
  try { const result = await api('/api/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({learning_mode: !learningMode})}); setMode(result.learning_mode); }
  catch (error) { toast(error.message); }
});

$('#more-hint').addEventListener('click', async () => {
  if (!activeId) return;
  try {
    const question = await api(`/api/questions/${encodeURIComponent(activeId)}`);
    if (question.hint_level >= 4) return;
    const level = question.hint_level + 1;
    await api(`/api/questions/${encodeURIComponent(activeId)}`, {method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({hint_level: level})});
    updateHint(level);
    sendMessage(`ヒントを一段階だけ進めてください。現在はレベル${level}です。正答は明かさないでください。`);
  } catch (error) { toast(error.message); }
});

$('#reveal-answer').addEventListener('click', async () => {
  if (!activeId) return;
  if (!confirm('正答と理由をAI講師に説明してもらいますか？')) return;
  try {
    await api(`/api/questions/${encodeURIComponent(activeId)}`, {method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({reveal_answer: true, hint_level: 5})});
    $('#answer-banner').classList.remove('hidden');
    $('#reveal-answer').textContent = '正答開示を許可済み';
    updateHint(5);
    sendMessage('正答を確認したいです。答えと、その理由を説明してください。');
  } catch (error) { toast(error.message); }
});

$('#subject-select').addEventListener('change', async (event) => {
  if (!activeId) return;
  try { await api(`/api/questions/${encodeURIComponent(activeId)}`, {method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({subject: event.target.value || null})}); $('#subject-label').textContent = event.target.value ? `· 科目${event.target.value}` : ''; await loadHistory(); }
  catch (error) { toast(error.message); }
});

$('#delete-question').addEventListener('click', async () => {
  if (!activeId || !confirm('この問題画像と会話履歴を削除しますか？')) return;
  try { await api(`/api/questions/${encodeURIComponent(activeId)}`, {method: 'DELETE'}); }
  catch (error) { toast(error.message); }
});

async function init() {
  try {
    const [settings] = await Promise.all([api('/api/settings'), loadHistory()]);
    setMode(settings.learning_mode);
    if (activeId) {
      try { await selectQuestion(activeId); }
      catch { showEmpty(); }
    }
  } catch (error) { toast(error.message); }
  updateAgentStatus();
  setInterval(updateAgentStatus, 10000);
  connectSocket();
}

init();
