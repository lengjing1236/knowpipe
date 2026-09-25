'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const labels = {fulltext: '完整正文', abstract: '仅摘要，全文待补齐', question_only: '仅问题，缺少解答',
    unverified: '完整性未验证', missing: '暂无正文', failed: '处理失败', not_requested: '尚未开始',
    queued: '等待处理', running: '处理中', ready: '已就绪', unavailable: '服务尚未配置',
    not_required: '无需处理', stale: '内容已更新，待重新处理'};
  const sourceLabels = {arxiv: 'arXiv 论文', stackexchange: 'Stack Exchange 问答', official_docs: '官方文档', tutorial: '教程',
    postgresql_docs: 'PostgreSQL 官方文档', docker_docs: 'Docker 官方文档', podcast: '播客文字稿', python_docs: 'Python 官方文档', django_docs: 'Django 官方文档'};
  const state = {mode: 'library', page: 1, limit: 10, q: '', source: '', total: 0};
  let csrf = null;
  let listRequest = 0;
  let detailRequest = 0;
  let recommendationRequest = 0;
  let recommendationTimer;
  let recommendationRendered;
  let profileRequest = 0;
  let goalEdited = false;
  let podcastRequest = 0;
  let podcastTimer;
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function message(text, error = false) {
    $('learning-message').textContent = text;
    $('learning-message').className = error ? 'error' : '';
    if ($('reading-dialog').open) {
      $('reading-message').textContent = text;
      $('reading-message').className = error ? 'error' : '';
    }
  }
  async function run(work, button) {
    if (button) button.disabled = true;
    try { await work(); } catch (error) { message(error.message || '请求失败，请重试', true); }
    finally { if (button) button.disabled = false; }
  }
  async function api(path, options = {}) {
    const method = options.method || 'GET';
    if (method !== 'GET' && !csrf) {
      const token = await fetch('/api/auth/csrf', {credentials: 'same-origin'});
      if (!token.ok) throw new Error('无法读取会话，请刷新页面');
      csrf = (await token.json()).csrf_token;
    }
    const response = await fetch(path, {...options, credentials: 'same-origin', headers: {
      'Content-Type': 'application/json', ...(method !== 'GET' ? {'X-CSRF-Token': csrf} : {})}});
    if (response.status === 401) { window.location.assign('/login'); throw new Error('请先登录'); }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const errors = {invalid_learning_input: '请检查填写内容和长度', not_found: '这份资料已不可用，请刷新列表',
        content_changed: '资料已更新，请重新打开资料后再标记已读',
        invalid_feed_url: '请输入可以公开访问的 RSS 地址', subscription_limit: '每人最多订阅 20 个节目，请先取消不再关注的订阅',
        csrf_failed: '会话已更新，请刷新页面再试', unexpected_user_id: '只能操作自己的学习记录'};
      throw new Error(errors[data.error] || '暂时无法完成操作，请稍后重试');
    }
    return data;
  }
  function action(text, work, className = 'quiet') {
    const button = node('button', text, className); button.type = 'button';
    button.addEventListener('click', () => run(() => work(button), button)); return button;
  }
  function originLink(url) {
    try {
      const parsed = new URL(url);
      if (!['http:', 'https:'].includes(parsed.protocol)) return node('span', '来源链接不可用');
      const link = node('a', '查看原始来源'); link.href = parsed.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; return link;
    } catch (_) { return node('span', '来源链接不可用'); }
  }
  function evidenceQuote(parent, evidence) {
    if (!evidence?.text) return;
    if (evidence.chinese_context) {
      parent.append(node('p', evidence.chinese_context_scope === 'native' ? '中文原文' : '对应翻译段落（可能包含片段周围的上下文）', 'muted'),
        node('blockquote', evidence.chinese_context));
      const original = node('details');
      original.append(node('summary', '核对原文片段'), node('blockquote', evidence.text));
      parent.append(original);
    } else {
      parent.append(node('blockquote', evidence.text));
    }
  }
  async function loadProfile(fill = false) {
    const requestNumber = ++profileRequest;
    const profile = await api('/api/learning/profile');
    if (requestNumber !== profileRequest) return;
    $('read-count').textContent = profile.read_count;
    $('saved-goal').textContent = profile.goal ? `已保存：${profile.goal.text}` : '还没有设置学习目标。';
    if (fill && !goalEdited) $('learning-goal').value = profile.goal?.text || '';
  }
  async function toggleRead(doc) {
    const payload = {source: doc.source, doc_id: doc.doc_id, read: !doc.is_read};
    if (payload.read) payload.content_version = doc.content_version;
    const result = await api('/api/learning/read-state', {method: 'PUT', body: JSON.stringify(payload)});
    doc.is_read = result.is_read;
    invalidateRecommendations();
    await Promise.all([loadProfile(), loadList(), loadRecommendations(), loadPodcasts()]);
    message(doc.is_read ? '已加入阅读记录。已读表示读过，不代表掌握。' : '已从阅读记录中移除。');
  }
  function versionNote(doc) {
    const status = doc.version_status || doc.read_version_status;
    return {changed: '资料已更新；你的记录仍对应之前的版本。', unknown: '这条历史记录的内容版本尚不明确。',
      unavailable: '原资料已不可用，你仍可撤销这条记录。'}[status];
  }
  async function showDocument(doc) {
    const requestNumber = ++detailRequest;
    const params = new URLSearchParams({source: doc.source, doc_id: doc.doc_id});
    const detail = await api(`/api/learning/document?${params}`);
    if (requestNumber !== detailRequest) return;
    $('reading-message').textContent = '';
    $('reading-title').textContent = detail.title || '未命名资料';
    const body = $('reading-content'); body.replaceChildren();
    body.append(node('p', labels[detail.content_status], 'pill'), originLink(detail.source_url));
    if (detail.quality?.contains_media || detail.quality?.media_urls?.length) body.append(node('p', '原资料包含图片等非文字内容；当前正文保留来源引用，图中信息需要打开原文查看。', 'muted'));
    if (detail.license) body.append(node('p', `来源许可：${detail.license}`, 'muted'));
    if (detail.authors?.length) {
      const credits = node('details'); credits.append(node('summary', '作者与出处'));
      detail.authors.forEach(author => {
        const line = node('p', `${author.name || '作者未注明'} · ${author.license || '许可版本见原文'} `);
        if (author.post_url || author.url) line.append(originLink(author.post_url || author.url)); credits.append(line);
      }); body.append(credits);
    }
    const note = versionNote(detail); if (note) body.append(node('p', note, 'muted'));
    const progress = node('div', undefined, 'processing-states');
    Object.entries({extraction: '正文提取', transcription: '语音转写', translation: '中文译文', analysis: '内容分析'}).forEach(([key, title]) => {
      progress.append(node('span', `${title}：${labels[detail.processing[key].status] || '状态待核验'}`, 'tag'));
    });
    body.append(progress);
    if (!detail.language.toLowerCase().startsWith('zh')) {
      const quality = detail.translation_quality || {};
      const note = quality.status === 'checks_passed' ? '数字和代码完整性检查通过；这不保证术语和语义翻译准确，请结合原文阅读。' :
        (quality.status === 'failed' ? '译文未通过完整性检查，暂不作为可阅读的中文全文发布。' : '译文完整性尚未检查。');
      const info = node('details'); info.append(node('summary', '自动翻译的检查与局限'), node('p', note));
      const issues = {number_missing: '译文遗漏或改变了原文中的数字。', code_missing: '译文没有完整保留原文代码。',
        number_added: '译文出现原文没有的数字。', identifier_missing: '译文遗漏或改变了技术标识符。',
        unknown_symbol: '译文含有无法正常生成的字符，可能遗漏了原文内容。'};
      (quality.issues || []).forEach(issue => { if (issues[issue]) info.append(node('p', issues[issue])); });
      if (detail.translation_processor) info.append(node('p', `译文版本标识：${detail.translation_processor}`, 'muted'));
      info.append(node('p', '自动译文未经人工校对，不代表教学质量已验证。', 'muted'));
      body.append(info);
    }
    const translationErrors = {
      translation_empty_output: '翻译模型没有返回有效译文，未发布不完整内容。',
      translation_truncated: '翻译输出未正常结束，未发布截断的译文。',
      translation_incomplete: '翻译结果未覆盖完整内容，尚未发布。',
      translation_invalid_input: '当前正文无法由此翻译模型处理。',
      translation_invalid_output: '译文为空或超出处理限制，尚未发布。',
      translation_timeout: '全文翻译超时，请稍后刷新推荐重试。',
      translation_model_unavailable: '翻译模型文件不可用，需要配置可用模型。',
      translation_language_not_supported: '当前本地模型不支持这篇资料的语言。',
      provider_not_configured: '翻译服务尚未配置。',
      translation_attempts_exhausted: '翻译重试次数已用完，可刷新推荐重新尝试。',
      translation_failed: '翻译未完成，可刷新推荐重试；未发布部分结果。'
    };
    translationErrors.translation_alignment_failed = '译文与原文位置对应不完整，未发布本次结果。';
    translationErrors.translation_quality_failed = '译文未通过完整性检查，尚未发布。';
    const translationError = translationErrors[detail.processing.translation.error_code];
    if (translationError) body.append(node('p', translationError, 'muted'));
    const text = node('div', detail.chinese_text || '中文全文尚未就绪。你可以查看原文与当前处理状态。', 'transcript');
    const chineseLabel = detail.language.toLowerCase().startsWith('zh') ? '中文原文' : '中文自动译文（未人工校对）';
    const format = node('p', detail.chinese_ready ? chineseLabel : '等待中文全文', 'muted');
    let original = false;
    const controls = node('div', undefined, 'actions');
    if (detail.original_text) controls.append(action('查看原文', button => {
      original = !original;
      text.textContent = original ? detail.original_text : (detail.chinese_text || '中文全文尚未就绪。');
      format.textContent = original ? `原文 · ${labels[detail.content_status]}` : (detail.chinese_ready ? chineseLabel : '等待中文全文');
      button.textContent = original ? '返回中文' : '查看原文';
    }));
    controls.append(action(detail.is_read ? '撤销已读' : '标记已读', async button => {
      await toggleRead(detail); button.textContent = detail.is_read ? '撤销已读' : '标记已读';
    }));
    body.append(controls, format, text);
    if (detail.chinese_ready && detail.translation_segments?.length) {
      const alignment = node('details', undefined, 'translation-alignment');
      alignment.append(node('summary', '逐段核对中文与原文'));
      alignment.addEventListener('toggle', () => {
        if (!alignment.open || alignment.dataset.rendered) return;
        alignment.dataset.rendered = 'true';
        // Python offsets count Unicode code points; JS String.slice counts UTF-16.
        const source = Array.from(detail.original_text);
        const target = Array.from(detail.chinese_text);
        detail.translation_segments.forEach(segment => {
          const translated = target.slice(segment.target_start, segment.target_end).join('');
          if (!translated.trim()) return;
          const section = node('section', undefined, 'aligned-paragraph');
          section.append(node('p', translated, 'transcript'),
            node('blockquote', source.slice(segment.source_start, segment.source_end).join(''), 'transcript'));
          alignment.append(section);
        });
      });
      body.append(alignment);
    }
    if (!$('reading-dialog').open) $('reading-dialog').showModal();
  }
  async function loadList() {
    const requestNumber = ++listRequest;
    const params = new URLSearchParams({page: state.page, limit: state.limit});
    if (state.mode === 'library') { if (state.q) params.set('q', state.q); if (state.source) params.set('source', state.source); }
    const data = await api(`/api/learning/${state.mode === 'history' ? 'history' : 'documents'}?${params}`);
    if (requestNumber !== listRequest) return;
    state.total = data.total;
    if (state.page > 1 && (state.page - 1) * state.limit >= data.total) {
      state.page = Math.max(1, Math.ceil(data.total / state.limit)); return loadList();
    }
    $('library-heading').textContent = state.mode === 'history' ? '我的已读资料' : '资料库';
    $('library-search').hidden = state.mode === 'history';
    $('show-library').setAttribute('aria-pressed', state.mode === 'library');
    $('history-tab').setAttribute('aria-pressed', state.mode === 'history');
    $('library-total').textContent = `共 ${data.total} 篇${state.mode === 'history' ? '已读资料' : '资料'}`;
    if (data.sources) {
      $('library-source').replaceChildren(new Option('全部来源', ''), ...data.sources.map(source => new Option(sourceLabels[source] || source, source)));
      $('library-source').value = state.source;
    }
    const list = $('library-items'); list.replaceChildren();
    data.items.forEach(doc => {
      const card = node('article', undefined, 'card');
      const meta = node('div', undefined, 'card-meta');
      meta.append(node('span', sourceLabels[doc.source] || doc.source), node('span', labels[doc.content_status] || '内容待核验', 'pill'));
      if (doc.is_read) meta.append(node('span', '已读', 'pill'));
      card.append(meta, node('h3', doc.title || doc.doc_id));
      const note = versionNote(doc); if (note) card.append(node('p', note, 'muted'));
      if (doc.read_at) card.append(node('small', `标记时间：${new Date(doc.read_at).toLocaleString('zh-CN')}`));
      const controls = node('div', undefined, 'actions');
      if (doc.version_status !== 'unavailable') controls.append(action('阅读资料', () => showDocument(doc)));
      controls.append(action(doc.is_read ? '撤销已读' : '标记已读', () => toggleRead(doc)));
      card.append(controls); list.append(card);
    });
    if (!data.items.length) list.append(node('p', state.mode === 'history' ? '还没有阅读记录。找到资料后，主动标记已读即可保存在这里。' : '没有找到资料。请调整筛选条件，或等待资料入库。', 'empty'));
    $('page-label').textContent = `第 ${state.page} / ${Math.max(1, Math.ceil(data.total / state.limit))} 页`;
    $('previous-page').disabled = state.page === 1;
    $('next-page').disabled = state.page * state.limit >= data.total;
  }
  async function switchMode(mode) { state.mode = mode; state.page = 1; await loadList(); }
  function invalidateRecommendations() {
    recommendationRequest++;
    podcastRequest++;
    $('learning-notifications').replaceChildren();
    clearTimeout(recommendationTimer);
    recommendationRendered = null;
    $('recommendation-items').replaceChildren();
    $('recommendation-status').textContent = '输入已更新，正在准备新的推荐…';
    $('recommendation-context').textContent = '';
  }
  async function loadRecommendations(refresh = false) {
    const requestNumber = ++recommendationRequest;
    clearTimeout(recommendationTimer);
    let data;
    try {
      data = await api('/api/learning/recommendations', refresh ? {method: 'POST', body: '{}'} : {});
    } catch (error) {
      if (requestNumber === recommendationRequest) recommendationTimer = setTimeout(() => {
        if (!document.hidden) run(() => loadRecommendations());
      }, 15000);
      throw error;
    }
    if (requestNumber !== recommendationRequest) return;
    const signature = JSON.stringify(data);
    if (signature !== recommendationRendered) {
      recommendationRendered = signature;
      const messages = {
        needs_goal: '先保存一个具体的学习目标，就可以开始推荐。',
        waiting_for_worker: '推荐服务暂未运行，已保存的目标会在服务恢复后处理。',
        waiting_for_corpus: '正在准备资料分析结果，完成后会自动推荐。',
        queued: '推荐已在等待处理，完成后会自动显示。',
        running: '正在结合学习目标和已读资料计算推荐…',
        ready: `找到 ${data.items.length} 篇可继续阅读的资料。`,
        empty: '暂时没有符合条件的推荐，可以调整目标或等待资料更新。',
        stale: '推荐依据的资料已更新，正在等待重新分析。',
        failed: '本次推荐未完成，可以点击“刷新推荐”重试。'
      };
      const emptyReasons = {no_fulltext: '资料库中还没有可用于分析的完整正文。',
        no_matching_terms: '现有正文中没有匹配到目标词语，请尝试更具体的技术名称。',
        empty_goal_terms: '请在目标中写明想学习的技术或问题。',
        no_relevant_candidates: '没有找到符合目标的未读资料。',
        no_object_candidates: '没有找到同时符合目标词语与指定技术对象的未读资料。可以查看下方查找解释，或等待资料补充。',
        redundant_candidates: '本次比较的候选片段与已读内容重合，暂未选出合适资料。'};
      const failures = {history_limit_exceeded: '已读记录超过当前可分析的 2,000 篇上限，本次未计算。',
        history_paragraph_limit_exceeded: '相关已读内容超过当前 5,000 个段落上限，本次未计算。',
        goal_term_limit_exceeded: '目标包含的不同词语过多，请聚焦到一个具体问题再试。',
        document_limit_exceeded: '资料库超过当前 50,000 篇分析上限，需要调整处理容量。',
        paragraph_limit_exceeded: '资料正文超过当前配置的段落分析上限，需要调整处理容量。'};
      $('recommendation-status').textContent = data.status === 'empty' ? (emptyReasons[data.reason] || messages.empty) :
        (data.status === 'failed' && failures[data.reason] ? failures[data.reason] : (messages[data.status] || '正在更新推荐状态。'));
      const corpus = data.corpus || {};
      const context = [];
      if (corpus.document_count !== null && corpus.document_count !== undefined) context.push(`分析范围：${corpus.document_count} 篇完整资料，${Object.keys(corpus.sources || {}).length} 个来源。`);
      if (data.status === 'ready') context.push(data.history_relevant_paragraphs ? `已参考 ${data.history_used} 篇版本可核对的已读资料。` : '暂无可对照的相关已读内容，本次依据学习目标推荐。');
      if (data.history_unavailable) context.push(`${data.history_unavailable} 条历史因资料或版本不可用未参与对照。`);
      const query = data.query;
      const semantic = data.semantic;
      if (semantic && !['ready', 'partial'].includes(semantic.status)) context.push('内容比较暂未就绪；下面若有结果，仅供按目标查找，不据此判断补充内容。');
      if (semantic?.status === 'partial') context.push('部分片段无法完成比较；补充说明只针对成功对照的内容。');
      if (semantic?.comparison_scope) context.push('比较范围为本次检索出的有限片段，不代表你的全部知识或资料全文。');
      if (semantic?.language === 'multilingual' && ['ready', 'partial'].includes(semantic.status)) {
        context.push('使用你保存的中文目标比较资料内容，原文依据见下方。');
      } else if (query?.status === 'translated') context.push(`同时使用中文原目标和自动英文解释查找：${query.variants[1]}。自动解释可能误译技术词，请核对推荐依据。`);
      if (query?.status === 'unavailable' || query?.status === 'failed') context.push('中文转英文查找目前未就绪，本次仅按原目标查找；没有推荐不表示资料库没有相关知识。');
      if (query?.entities?.length) context.push(`资料须对应指定技术：${query.entities.map(item => item.name).join('、')}。名称或官方来源只说明讨论范围，仍需核对正文内容。`);
      $('recommendation-context').textContent = context.join(' ');
      const list = $('recommendation-items'); list.replaceChildren();
      list.dataset.revision = String(data.input_revision);
      data.items.forEach(doc => {
        const card = node('article', undefined, 'card recommendation-card');
        const meta = node('div', undefined, 'card-meta');
        meta.append(node('span', sourceLabels[doc.source] || doc.source), node('span', doc.chinese_ready ? '中文可读' : `中文全文：${labels[doc.translation_status] || '准备中'}`, 'pill'));
        card.append(meta, node('h3', doc.title || doc.doc_id));
        const comparisonLabels = {possible_supplement: '相对本次对照的已读片段，这篇可能补充了相关内容。请核对下方双方依据。',
          covered: '本次比较发现部分内容已被已读资料覆盖，因此降低了优先级。',
          uncertain: '与目标有关，但目前无法可靠判断它是否补充了已读内容。',
          goal_only: '按学习目标推荐；当前没有足够的相关已读片段作对照。'};
        card.append(node('p', comparisonLabels[doc.comparison?.status] || '按目标词语查找到的候选资料，请先核对正文是否有帮助。'));
        const details = node('details', undefined, 'recommendation-evidence');
        details.append(node('summary', '为什么推荐这篇？查看原文依据'));
        details.append(node('p', '与目标相关的正文依据', 'muted'));
        evidenceQuote(details, doc.goal_evidence);
        (doc.entity_evidence || []).forEach(evidence => details.append(
          node('p', `技术对象 ${evidence.name} 的${evidence.field === 'source_url' ? '官方来源' : evidence.field === 'title' ? '标题' : '正文'}依据`, 'muted'),
          node('blockquote', evidence.text)));
        if (doc.history_evidence) {
          details.append(node('p', `用于对照的已读资料：${doc.history_evidence.history.title}`, 'muted'));
          evidenceQuote(details, doc.history_evidence.history);
          if (doc.history_evidence.candidate.text !== doc.goal_evidence.text) details.append(
            node('p', '本篇中用于对照的原文片段', 'muted'), node('blockquote', doc.history_evidence.candidate.text));
          details.append(node('p', '对照依据只用于减少重复，不能判断你是否已经掌握内容。', 'muted'));
        } else {
          details.append(node('p', '当前没有可展示的已读片段对照，不据此判断这是你未学过的知识。', 'muted'));
        }
        const comparison = doc.comparison;
        if (comparison?.candidate && comparison?.history) {
          details.append(node('p', '本次候选补充／覆盖判断的双方片段', 'muted'));
          evidenceQuote(details, comparison.candidate);
          details.append(node('p', `对照已读：${comparison.history.title || '已读资料'}`, 'muted'));
          evidenceQuote(details, comparison.history);
          if (comparison.limitation) details.append(node('p', comparison.limitation, 'muted'));
          details.append(action('打开对照的已读资料', () => showDocument(comparison.history)));
        }
        if (comparison?.covered_evidence?.length && comparison.status === 'possible_supplement') {
          const covered = node('details'); covered.append(node('summary', '这篇资料也包含哪些已读过的相似说明？'));
          comparison.covered_evidence.forEach(pair => {
            covered.append(node('p', '本篇片段', 'muted'));
            evidenceQuote(covered, pair.candidate);
            covered.append(node('p', `对应已读：${pair.history?.title || '已读资料'}`, 'muted'));
            evidenceQuote(covered, pair.history);
          });
          details.append(covered);
        }
        const supplement = doc.supplement_evidence;
        if (supplement?.status === 'lexical_candidate') {
          details.append(node('p', '可能补充的内容：候选原文片段', 'muted'), node('blockquote', supplement.candidate.text));
          if (supplement.comparison) details.append(node('p', `对照已读资料：${supplement.comparison.title}`, 'muted'),
            node('blockquote', supplement.comparison.text));
          details.append(node('p', `相关已读片段中没有出现的词语：${supplement.additional_terms.join('、')}`),
            node('p', supplement.limitation, 'muted'));
        }
        card.append(details);
        const controls = node('div', undefined, 'actions');
        controls.append(action(doc.chinese_ready ? '阅读中文全文' : '查看资料与处理状态', () => showDocument(doc)), originLink(doc.source_url));
        card.append(controls); list.append(card);
      });
    }
    const delay = ['running', 'queued', 'waiting_for_corpus', 'stale'].includes(data.status) ? 4000 : 15000;
    recommendationTimer = setTimeout(() => {
      if (!document.hidden) run(() => loadRecommendations());
    }, delay);
  }
  async function loadPodcasts() {
    const requestNumber = ++podcastRequest;
    clearTimeout(podcastTimer);
    try {
      const [subscriptions, episodes, notifications] = await Promise.all([
        api('/api/podcasts/subscriptions'), api('/api/podcasts/episodes'), api('/api/notifications')]);
      if (requestNumber !== podcastRequest) return;
      $('podcast-status').textContent = `已订阅 ${subscriptions.items.length} 个节目。约每 5 分钟检查更新，首次获取最近 3 期；转写与翻译需要额外处理时间。`;
      const subs = $('learning-subscriptions'); subs.replaceChildren();
      subscriptions.items.forEach(feed => {
        const row = node('div', undefined, 'actions'); row.append(node('span', feed.title || feed.url));
        if (feed.last_error) row.append(node('span', '暂时无法获取订阅，稍后自动重试', 'muted'));
        row.append(action('取消订阅', async () => { await api(`/api/podcasts/subscriptions/${feed.feed_id}`, {method: 'DELETE'}); await loadPodcasts(); })); subs.append(row);
      });
      const notices = $('learning-notifications'); notices.replaceChildren();
      const personal = notifications.items.filter(n => n.kind === 'learning_recommendation');
      personal.forEach(notice => {
        const card = node('article', undefined, 'card'); card.append(node('h4', notice.title),
          node('p', notice.recommendation_mode === 'supplement' ? '与目标相关，并找到了可与已读资料比较的补充片段。' : '与你的学习目标相关，中文全文已准备好。'));
        card.append(action('阅读中文全文', async () => {
          await showDocument(notice);
          await api(`/api/notifications/${notice.id}/read`, {method: 'POST', body: '{}'});
          // Reading a notification does not mark a document as read.
          await loadPodcasts();
        })); notices.append(card);
      });
      if (!personal.length) notices.append(node('p', '暂时没有新的个人推荐。全部订阅更新和处理进度可在下方查看。', 'empty'));
      const list = $('learning-episodes'); list.replaceChildren();
      const stages = {queued: '等待获取文字稿或转写音频', processing: '正在处理文字稿', ready: '全文已入库，等待或已完成推荐分析',
        failed: '处理未完成', awaiting_transcript: '暂无可用音频或文字稿'};
      const errors = {transcription_unavailable: '本地转写模型尚未配置', incomplete_transcription: '未取得完整转写结果',
        audio_too_large: '音频超过大小限制', audio_too_long: '音频超过时长限制',
        audio_duration_exceeded: '音频时长不符合处理限制', audio_download_timeout: '音频下载超时', audio_decode_failed: '音频无法完整解码'};
      episodes.items.forEach(episode => {
        const card = node('article', undefined, 'card'); card.append(node('h4', episode.title),
          node('p', `${stages[episode.status] || '等待处理'}${episode.error_code ? ` · ${errors[episode.error_code] || '后台将进行有限次数重试'}` : ''}`, 'muted'));
        if (episode.learning_content) {
          const content = episode.learning_content;
          card.append(node('p', `内容分析：${labels[content.processing.analysis.status] || '待核验'} · 中文全文：${content.chinese_ready ? '已就绪' : (labels[content.processing.translation.status] || '准备中')}`, 'muted'));
        }
        if (episode.document_id) card.append(action('查看正文与中文准备状态', () => showDocument({source: 'podcast', doc_id: episode.document_id})));
        if (episode.status === 'failed') card.append(action('重试处理', async () => {
          await api(`/api/podcasts/episodes/${episode.episode_id}/retry`, {method: 'POST', body: '{}'}); await loadPodcasts();
        })); list.append(card);
      });
      if (!episodes.items.length) list.append(node('p', '尚未发现节目，后台检查订阅后会自动显示。', 'empty'));
    } finally {
      if (requestNumber === podcastRequest) podcastTimer = setTimeout(() => { if (!document.hidden) run(loadPodcasts); }, 15000);
    }
  }
  $('podcast-subscribe-form').addEventListener('submit', event => {
    event.preventDefault(); run(async () => {
      await api('/api/podcasts/subscriptions', {method: 'POST', body: JSON.stringify({url: $('podcast-feed-url').value.trim()})});
      $('podcast-feed-url').value = ''; await loadPodcasts();
    }, event.submitter);
  });
  $('podcast-refresh').addEventListener('click', () => run(loadPodcasts));
  $('goal-form').addEventListener('submit', event => {
    event.preventDefault(); run(async () => {
      await api('/api/learning/goal', {method: 'PUT', body: JSON.stringify({text: $('learning-goal').value})});
      invalidateRecommendations();
      await Promise.all([loadProfile(), loadRecommendations(), loadPodcasts()]); message('学习目标已保存，推荐会按新目标更新。');
    }, event.submitter);
  });
  $('learning-goal').addEventListener('input', () => { goalEdited = true; });
  $('library-search').addEventListener('submit', event => {
    event.preventDefault(); state.q = $('library-query').value.trim(); state.source = $('library-source').value; state.page = 1;
    run(loadList, event.submitter);
  });
  $('show-library').addEventListener('click', () => run(() => switchMode('library')));
  ['show-history', 'history-tab'].forEach(id => $(id).addEventListener('click', () => run(() => switchMode('history'))));
  $('library-refresh').addEventListener('click', () => run(() => Promise.all([loadProfile(), loadList()])));
  $('recommendation-refresh').addEventListener('click', event => run(() => loadRecommendations(true), event.currentTarget));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) run(() => Promise.all([loadRecommendations(), loadPodcasts()])); });
  $('previous-page').addEventListener('click', () => { if (state.page > 1) { state.page--; run(loadList); } });
  $('next-page').addEventListener('click', () => { if (state.page * state.limit < state.total) { state.page++; run(loadList); } });
  $('reading-close').addEventListener('click', () => { detailRequest++; $('reading-dialog').close(); });
  $('learning-logout').addEventListener('click', () => run(async () => {
    await api('/api/auth/logout', {method: 'POST', body: '{}'}); window.location.assign('/login');
  }));
  run(async () => {
    const user = await api('/api/auth/me'); $('learning-user').textContent = user.username;
    await Promise.all([loadProfile(true), loadList(), loadRecommendations(), loadPodcasts()]);
    const params = new URLSearchParams(window.location.search);
    if (params.get('source') && params.get('doc_id')) await showDocument({source: params.get('source'), doc_id: params.get('doc_id')});
  });
})();
