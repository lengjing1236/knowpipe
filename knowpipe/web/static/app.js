'use strict';
let csrfToken = null;
let currentUser = null;
let eventStream = null;
const stateLabels = {queued: '等待处理', processing: '分析中', ready: '分析完成', awaiting_transcript: '等待文字稿', failed: '处理失败', new: '新知识', refine: '深化阅读', known: '已了解', possible_conflict: '待核对', success: '成功', running: '运行中'};
const notificationItems = new Map();
let notificationPoll = null;

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function message(text, error = false) {
  const node = document.getElementById('message');
  node.textContent = text;
  node.className = error ? 'error' : '';
}
function action(text, fn, className = 'quiet') {
  const button = el('button', text, className);
  button.type = 'button';
  button.addEventListener('click', () => run(fn, button));
  return button;
}
async function run(fn, button) {
  if (button) button.disabled = true;
  try { await fn(); } catch (error) { message(error.message || '请求失败，请稍后重试', true); }
  finally { if (button) button.disabled = false; }
}
async function refreshCsrf() {
  const response = await fetch('/api/auth/csrf', {credentials: 'same-origin'});
  if (!response.ok) throw new Error('无法连接服务，请稍后重试');
  csrfToken = (await response.json()).csrf_token;
}
async function api(path, options = {}) {
  const method = options.method || 'GET';
  if (method !== 'GET' && !csrfToken) await refreshCsrf();
  const response = await fetch(path, {...options, credentials: 'same-origin', headers: {
    'Content-Type': 'application/json', ...(method !== 'GET' ? {'X-CSRF-Token': csrfToken} : {})}});
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && document.getElementById('workspace')) {
    window.location.href = '/login';
    throw new Error('登录已过期');
  }
  if (!response.ok) {
    const errors = {invalid_credentials: '用户名或密码不正确', username_taken: '用户名已被使用',
      csrf_failed: '页面会话已更新，请刷新页面后重试', rate_limited: '操作过于频繁，请一分钟后重试',
      invalid_feed_url: '请输入可公开访问的 HTTP(S) RSS 地址', subscription_limit: '最多订阅 20 个播客',
      invalid_transcript: '文字稿需要 20 至 400,000 个字符', episode_not_editable: '节目状态已变化，请刷新后重试'};
    throw new Error(errors[data.error] || `请求未完成（${data.error || response.status}）`);
  }
  return data;
}
function safeLink(url, text) {
  try {
    const parsed = new URL(url);
    if (!['https:', 'http:'].includes(parsed.protocol)) return el('span', text);
    const a = el('a', text); a.href = parsed.href; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
  } catch (_) { return el('span', text); }
}
function dateText(value) { return value ? new Date(value).toLocaleString('zh-CN') : '暂无时间信息'; }
function empty(node, text) { node.replaceChildren(el('p', text, 'empty')); }
function dialog(title) {
  document.getElementById('detail-title').textContent = title;
  const body = document.getElementById('detail-body'); body.replaceChildren();
  document.getElementById('detail-dialog').showModal(); return body;
}

async function loadStats() {
  const data = await api('/api/stats');
  document.getElementById('metric-docs').textContent = data.documents.toLocaleString();
  document.getElementById('metric-episodes').textContent = data.podcast_episodes;
  document.getElementById('metric-batch').textContent = stateLabels[data.batches[0]?.status] || '尚未运行';
  document.getElementById('source-counts').textContent = Object.entries(data.sources).map(([s, n]) => `${s} · ${n.toLocaleString()} 篇`).join(' / ') || '尚未导入技术文献。';
  const list = document.getElementById('batches-list'); list.replaceChildren();
  data.batches.forEach(batch => {
    const row = el('div', undefined, 'batch-row');
    row.append(el('strong', `${batch.batch_id} · ${stateLabels[batch.status] || batch.status}`));
    row.append(el('small', `输入 ${batch.input_count || 0} / 有效 ${batch.valid_count || 0} / 失败 ${batch.failed_count || 0} · ${dateText(batch.finished_at)}`));
    if (batch.spark_application_id) row.append(el('small', `Spark ${batch.spark_application_id} · ${batch.spark_master} · ${batch.segment_count || 0} 段`));
    list.append(row);
  });
  if (!data.batches.length) empty(list, '完成一次处理后，这里将显示真实批次记录。');
}
async function loadTopics() {
  const data = await api('/api/topics');
  const list = document.getElementById('topics-list'); list.replaceChildren();
  const known = new Set(currentUser.known_topics.map(String));
  data.topics.forEach(topic => {
    const li = el('li'); const label = el('label'); const input = el('input');
    input.type = 'checkbox'; input.value = topic.topic_cluster_id; input.checked = known.has(String(topic.topic_cluster_id));
    label.append(input, el('span', `${topic.top_keywords.join(' / ')} · ${topic.document_count} 篇`));
    li.append(label); list.append(li);
  });
  if (!data.topics.length) empty(list, '文献分析完成后可选择主题。');
}
async function loadRecommendations() {
  const list = document.getElementById('recommendations-list');
  const params = new URLSearchParams({user_id: currentUser.user_id, limit: '20', mode: document.getElementById('mode-filter').value});
  const source = document.getElementById('source-filter').value; if (source) params.set('source', source);
  const data = await api(`/api/recommendations?${params}`); list.replaceChildren();
  data.items.forEach(item => {
    const card = el('article', undefined, 'card'); const doc = item.document || item;
    const meta = el('div', undefined, 'card-meta');
    meta.append(el('span', stateLabels[item.status] || '对照基线', 'pill'), el('span', doc.source || '文献'));
    card.append(meta, el('h3', doc.title || doc.doc_id || item.knowledge_id));
    card.append(el('p', `匹配关键词：${(item.matched_keywords || []).join('、') || '未命中已知关键词'} · 分数 ${Number(item.score || 0).toFixed(3)}`, 'muted'));
    const actions = el('div', undefined, 'actions');
    if (doc.source && doc.doc_id) actions.append(action('阅读详情', async () => {
      const detail = await api(`/api/documents/${encodeURIComponent(doc.source)}/${encodeURIComponent(doc.doc_id)}`);
      const body = dialog(detail.title); body.append(safeLink(detail.source_url, '查看原始来源'), el('p', detail.body_text || '暂无正文', 'transcript'));
    }));
    if (item.knowledge_id) actions.append(action('我已了解', async () => {
      await api('/api/profile/feedback', {method: 'POST', body: JSON.stringify({user_id: currentUser.user_id, knowledge_id: item.knowledge_id, action: 'confirmed_known'})});
      await loadRecommendations(); message('已记录学习反馈。');
    }));
    card.append(actions); list.append(card);
  });
  if (!data.items.length) empty(list, '暂无推荐。文献入库并完成 Spark 处理后，推荐会出现在这里。');
}
async function loadPodcasts() {
  const [subscriptions, episodes] = await Promise.all([api('/api/podcasts/subscriptions'), api('/api/podcasts/episodes')]);
  document.getElementById('metric-feeds').textContent = subscriptions.items.length;
  const feeds = document.getElementById('subscriptions-list'); feeds.replaceChildren();
  subscriptions.items.forEach(feed => {
    const li = el('li'); li.append(el('strong', feed.title), el('small', feed.last_error ? '暂时无法读取 RSS，稍后自动重试' : `上次检查：${dateText(feed.last_checked_at)}`));
    li.append(action('取消订阅', async () => { await api(`/api/podcasts/subscriptions/${feed.feed_id}`, {method: 'DELETE'}); await loadPodcasts(); })); feeds.append(li);
  });
  if (!subscriptions.items.length) empty(feeds, '尚未订阅播客');
  const list = document.getElementById('episodes-list'); list.replaceChildren();
  episodes.items.forEach(episode => {
    const card = el('article', undefined, 'card'); const meta = el('div', undefined, 'card-meta');
    meta.append(el('span', stateLabels[episode.status] || episode.status, `pill ${episode.status}`), el('span', dateText(episode.published_at)));
    card.append(meta, el('h3', episode.title));
    if (episode.status === 'awaiting_transcript') card.append(el('p', '发布者尚未提供可读取的文字稿。可补充文字稿后继续分析。', 'muted'));
    if (episode.error_code) card.append(el('p', `处理未完成：${episode.error_code}`, 'muted'));
    card.append(action(episode.status === 'ready' ? '阅读文字稿与分析' : '查看节目', () => showEpisode(episode.episode_id))); list.append(card);
  });
  if (!episodes.items.length) empty(list, subscriptions.items.length ? '已订阅，后台正在等待下一次检查。可稍后刷新。' : '添加一个公开 RSS，开始接收新节目。');
}
async function showEpisode(id) {
  const episode = await api(`/api/podcasts/episodes/${id}`);
  const body = dialog(episode.title);
  body.append(el('p', stateLabels[episode.status] || episode.status, 'pill'), safeLink(episode.source_url, ' 查看节目来源'));
  if (['awaiting_transcript', 'failed'].includes(episode.status)) {
    const form = el('form'); const label = el('label', '补充你有权使用的文字稿（20–400,000 字符）'); const text = el('textarea');
    text.rows = 8; text.required = true; text.minLength = 20; text.maxLength = 400000; label.append(text);
    const submit = el('button', '提交并分析'); submit.type = 'submit'; form.append(label, submit);
    form.addEventListener('submit', event => { event.preventDefault(); run(async () => {
      await api(`/api/podcasts/episodes/${id}/transcript`, {method: 'POST', body: JSON.stringify({text: text.value})});
      document.getElementById('detail-dialog').close(); await loadPodcasts(); message('文字稿已加入处理队列。');
    }, submit); }); body.append(form);
  }
  if (episode.analysis) {
    const analysis = episode.analysis;
    body.append(el('p', `批次 ${episode.batch_id} · ${analysis.segments.length} 段 · ${analysis.weighting === 'tfidf' ? 'TF-IDF' : '词频（样本不足）'} · ${analysis.topic_mode === 'kmeans' ? 'KMeans 主题分组' : '单组，不作多主题聚类'}`, 'muted'));
    body.append(el('p', `文字稿来源：${episode.transcript_origin === 'user_supplied' ? '用户补充' : '节目发布者'} · Spark ${analysis.spark_application_id}`, 'footnote'));
    const full = el('details'); full.append(el('summary', '展开完整文字稿'), el('p', episode.transcript, 'transcript')); body.append(full);
    analysis.segments.forEach(segment => {
      const card = el('section', undefined, 'segment'); card.id = `segment-${segment.segment_id}`;
      card.append(el('h3', `片段 ${segment.segment_id + 1} · 本批次主题 ${segment.cluster_id + 1}`));
      const keywords = el('div'); segment.keywords.forEach(k => { const tag = el('span', k.term, 'tag'); tag.title = `${segment.keyword_weighting === 'term_frequency' ? '词频' : 'TF-IDF'} 权重 ${k.weight.toFixed(4)}`; keywords.append(tag); });
      card.append(keywords, el('p', segment.text));
      if (segment.similar_segments.length) {
        const links = el('div', '相关片段：', 'actions');
        segment.similar_segments.forEach(s => links.append(action(`${s.segment_id + 1}（${s.score.toFixed(2)}）`, () => document.getElementById(`segment-${s.segment_id}`).scrollIntoView({behavior: 'smooth'}))));
        card.append(links);
      }
      body.append(card);
    });
  }
}
function renderNotifications() {
  const list = document.getElementById('notifications-list'); list.replaceChildren();
  [...notificationItems.values()].sort((a, b) => b.id.localeCompare(a.id)).slice(0, 50).forEach(item => {
    const li = el('li'); li.append(el('small', item.read ? '已读' : '新文字稿分析完成'));
    li.append(action(item.title, async () => {
      await showEpisode(item.episode_id);
      await api(`/api/notifications/${item.id}/read`, {method: 'POST', body: '{}'});
      item.read = true; renderNotifications();
    })); list.append(li);
  });
  if (!notificationItems.size) empty(list, '处理完成后会在这里通知你。');
}
async function startNotifications() {
  const data = await api('/api/notifications'); data.items.forEach(item => notificationItems.set(item.id, item)); renderNotifications();
  // Temporary HTTPS tunnels may not support SSE. Persisted notifications also work by polling.
  let polling = false;
  async function pollNotifications() {
    if (polling || document.hidden) return;
    polling = true;
    try {
      const data = await api('/api/notifications');
      const fresh = data.items.filter(item => !notificationItems.has(item.id));
      data.items.forEach(item => notificationItems.set(item.id, item));
      renderNotifications();
      if (fresh.length) {
        await Promise.all([loadPodcasts(), loadStats()]);
        message(`新分析已完成：${fresh[0].title}`);
      }
      if (eventStream?.readyState !== EventSource.OPEN) document.getElementById('notification-status').textContent = '定时检查中';
    } finally { polling = false; }
  }
  notificationPoll = setInterval(() => run(pollNotifications), 5000);
  eventStream = new EventSource('/api/notifications/stream');
  eventStream.onopen = () => { document.getElementById('notification-status').textContent = '已连接'; };
  eventStream.onerror = () => { document.getElementById('notification-status').textContent = '重连中'; };
  eventStream.addEventListener('notification', event => {
    const item = JSON.parse(event.data); const isNew = !notificationItems.has(item.id);
    notificationItems.set(item.id, item); renderNotifications();
    if (isNew) run(async () => { await Promise.all([loadPodcasts(), loadStats()]); message(`新分析已完成：${item.title}`); });
  });
}
const loginForm = document.getElementById('login-form');
if (loginForm) loginForm.addEventListener('submit', event => {
  event.preventDefault(); const mode = event.submitter?.dataset.action || 'login';
  run(async () => {
    await api(`/api/auth/${mode}`, {method: 'POST', body: JSON.stringify({username: document.getElementById('username').value, password: document.getElementById('password').value})});
    if (mode === 'login') window.location.href = '/'; else message('注册成功，请点击登录。');
  }, event.submitter);
});
if (document.getElementById('workspace')) run(async () => {
  currentUser = await api('/api/auth/me'); await refreshCsrf();
  document.getElementById('username-label').textContent = currentUser.username;
  document.getElementById('close-detail').addEventListener('click', () => document.getElementById('detail-dialog').close());
  document.getElementById('logout').addEventListener('click', () => run(async () => { await api('/api/auth/logout', {method: 'POST', body: '{}'}); eventStream?.close(); window.location.href = '/login'; }));
  document.getElementById('subscribe-form').addEventListener('submit', event => {
    event.preventDefault(); run(async () => { await api('/api/podcasts/subscriptions', {method: 'POST', body: JSON.stringify({url: document.getElementById('feed-url').value})}); document.getElementById('feed-url').value = ''; await loadPodcasts(); message('已添加订阅，后台将检查新节目。'); }, event.submitter);
  });
  document.getElementById('save-topics').addEventListener('click', event => run(async () => {
    const topics = [...document.querySelectorAll('#topics-list input:checked')].map(input => input.value);
    await api('/api/profile/topics', {method: 'POST', body: JSON.stringify({user_id: currentUser.user_id, known_topics: topics})});
    currentUser.known_topics = topics; await loadRecommendations(); message('已保存已知主题。');
  }, event.currentTarget));
  ['source-filter', 'mode-filter'].forEach(id => document.getElementById(id).addEventListener('change', () => run(loadRecommendations)));
  document.getElementById('refresh-podcasts').addEventListener('click', event => run(loadPodcasts, event.currentTarget));
  await Promise.all([loadStats(), loadTopics(), loadRecommendations(), loadPodcasts(), startNotifications()]);
  setInterval(() => { if (!document.hidden) run(loadPodcasts); }, 30000);
});
window.addEventListener('pagehide', () => { eventStream?.close(); clearInterval(notificationPoll); });
