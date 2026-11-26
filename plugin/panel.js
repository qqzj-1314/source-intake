(() => {
  const $ = id => document.getElementById(id);
  const KINDS = ['csp', 'js', 'css', 'cls'];
  const KIND_TEXT = { csp: 'CSP', js: 'JS', css: 'CSS', cls: 'CLS', attachment: '附件' };
  const DEFAULT_ROUTES = {
    hisui: {
      csp: 'frontend/csp',
      js: 'frontend/scripts/dhcnewpro/dhcemc',
      css: 'frontend/scripts/dhcnewpro/dhcemc/css',
      cls: 'backend',
    },
    flat: { csp: '', js: '', css: '', cls: '' },
  };
  const STATUS_TEXT = {
    ready: '可导入', same: '内容一致', exists: '同名文件', pending: '待补充', invalid: '无效', conflict: '目标重复',
    written: '已写入', replaced: '已替换', 'skipped-same': '已跳过（一致）', 'skipped-existing': '已跳过（同名）',
    failed: '失败', 'rolled-back': '已回滚', 'rollback-failed': '回滚失败', 'skipped-not-attempted': '未执行',
    blocked: '整批阻塞', completed: '完成', 'no-write': '无写入', 'tracking-failed': '清单保存失败',
  };
  const state = {
    ready: false, busy: false, settings: null, defaults: { profile: 'hisui', routes: DEFAULT_ROUTES.hisui, cls_layout: 'package' },
    root: '', projectName: '', profile: 'hisui', routes: { ...DEFAULT_ROUTES.hisui }, clsLayout: 'package',
    requirement: { id: '', title: '', description: '', url: '', prompt: '' },
    sources: [], nextSourceId: 1, sessionId: randomId(), preview: null, previewBlocked: false, revision: 0,
  };
  const rowRefs = new Map();
  const IMAGE_NAME = /\.(png|jpe?g|gif|webp|bmp)$/i;
  let activeContentId = null;
  let contentPreviewRevision = 0;
  const imagePasteQueue = [];
  let processingImagePaste = false;
  let queuedImages = 0;

  async function imageUrl(entry) {
    // Re-read on every explicit preview so external file edits do not show a stale image.
    const image = await hostCall('panel_image_preview', { path: entry.path });
    if (!/^data:image\/(png|jpeg|gif|webp|bmp);base64,/.test(image?.data_url || '')) throw new Error('图片预览返回无效。');
    return image.data_url;
  }

  async function showContent(entry) {
    activeContentId = entry.id;
    const requestRevision = ++contentPreviewRevision;
    const dialog = $('content-preview');
    $('content-preview-title').textContent = entry.filename || (entry.kind === 'text' ? `粘贴内容 #${entry.number}` : basename(entry.path));
    $('content-code').hidden = true;
    $('image-scroll').hidden = true;
    $('image-size').hidden = true;
    $('content-preview-error').hidden = true;
    $('content-image').removeAttribute('src');
    $('content-image').classList.remove('original');
    $('image-size').textContent = '原始尺寸';
    if (!dialog.open) dialog.showModal();
    if (entry.kind === 'text') {
      $('content-preview-meta').textContent = `${entry.text.length} 字符 · 只读原文预览`;
      $('content-code').textContent = entry.text;
      $('content-code').hidden = false;
      return;
    }
    $('content-preview-meta').textContent = '正在加载图片…';
    try {
      const url = await imageUrl(entry);
      if (activeContentId !== entry.id || requestRevision !== contentPreviewRevision || !dialog.open) return;
      entry.imageUrl = url;
      const image = $('content-image');
      image.onload = () => {
        if (activeContentId === entry.id && requestRevision === contentPreviewRevision) $('content-preview-meta').textContent = `${image.naturalWidth} × ${image.naturalHeight} · 图片预览`;
      };
      image.onerror = () => {
        if (activeContentId !== entry.id || requestRevision !== contentPreviewRevision) return;
        $('content-preview-meta').textContent = '无法预览图片';
        $('image-size').hidden = true;
        $('content-preview-error').textContent = '浏览器无法解码这张图片，附件仍保留；可保存为 PNG/JPEG 后重新添加。';
        $('content-preview-error').hidden = false;
      };
      image.src = url;
      $('image-scroll').hidden = false;
      $('image-size').hidden = false;
    } catch (error) {
      if (activeContentId !== entry.id || requestRevision !== contentPreviewRevision || !dialog.open) return;
      $('content-preview-meta').textContent = '';
      $('content-preview-error').textContent = error.message;
      $('content-preview-error').hidden = false;
    }
  }

  function randomId() {
    if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
    return `panel-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function hostCall(name, args = {}) {
    if (!window.sourceIntakeHost) return Promise.reject(new Error('请从 Codex 左侧的源码导入入口打开面板'));
    return window.sourceIntakeHost.call(name, args);
  }

  const rootInput = $('root');
  const projectNameInput = $('project-name');
  const recentSelect = $('recent-roots');
  const profileSelect = $('profile');
  const clsSelect = $('cls-layout');
  const routeInputs = { csp: $('route-csp'), js: $('route-js'), css: $('route-css'), cls: $('route-cls') };
  const requirementInputs = {
    id: $('requirement-id'), title: $('requirement-title'),
    description: $('requirement-description'), url: $('requirement-url'), prompt: $('requirement-prompt'),
  };
  const conflictSelect = $('conflict');
  const sourceList = $('source-list');
  const sourceEmpty = $('source-empty');
  const sourceCount = $('source-count');
  const pasteBox = $('paste-box');
  const pasteFilename = $('paste-filename');
  const pasteText = $('paste-text');
  const previewSummary = $('preview-summary');
  const resultBox = $('result');
  const statusLine = $('status');
  const previewButton = $('preview');
  const importButton = $('import');
  const buttons = ['choose-directory', 'collect-clipboard', 'choose-files', 'paste-code', 'add-paste', 'cancel-paste', 'preview', 'create-empty', 'choose-attachments', 'attachment-clipboard', 'capture-screenshot', 'search-requirements']
    .map(id => $(id));

  function setConnection(kind, text) {
    $('online-dot').className = `dot${kind ? ` ${kind}` : ''}`;
    $('connection-label').textContent = text;
  }

  function setStatus(text, error = false) {
    statusLine.textContent = text || '';
    statusLine.className = `status-line${error ? ' error' : ''}`;
  }

  function setSummary(text, tone = '') {
    previewSummary.textContent = text;
    previewSummary.className = `summary${tone ? ` ${tone}` : ''}`;
  }

  function updateControls() {
    const disabled = state.busy || !state.ready;
    for (const button of buttons) button.disabled = disabled;
    previewButton.disabled = disabled;
    importButton.disabled = disabled || !state.preview || state.previewBlocked;
  }

  function normalizeRoot(value) {
    return String(value || '').trim().replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase();
  }

  function basename(value) {
    const parts = String(value || '').trim().split(/[\\/]+/).filter(Boolean);
    return parts.length ? parts[parts.length - 1] : '';
  }

  function extensionKind(name) {
    const match = /\.([^.\\/]+)$/.exec(String(name || ''));
    if (!match) return '';
    const ext = `.${match[1].toLowerCase()}`;
    return KINDS.find(kind => ext === `.${kind}`) || '';
  }

  function findProject(root) {
    const key = normalizeRoot(root);
    if (!key || !state.settings?.projects) return null;
    for (const project of Object.values(state.settings.projects)) {
      if (project && normalizeRoot(project.root) === key) return project;
    }
    return null;
  }

  function refreshRecentSelect() {
    const current = state.root.trim();
    clear(recentSelect);
    recentSelect.appendChild(el('option', '', '选择已保存的目录')).value = '';
    for (const root of state.settings?.recent_roots || []) {
      const option = el('option', '', root);
      option.value = root;
      recentSelect.appendChild(option);
    }
    recentSelect.value = (state.settings?.recent_roots || []).includes(current) ? current : '';
  }

  function updateRouteInputs() {
    for (const kind of KINDS) routeInputs[kind].value = state.routes[kind] ?? '';
  }

  function applySettings(data) {
    if (!data) return;
    state.settings = data.settings || { recent_roots: [], projects: {}, warnings: [] };
    if (data.defaults) state.defaults = data.defaults;
    refreshRecentSelect();
  }

  function applyProjectRule(project) {
    if (!project) return false;
    state.profile = project.profile === 'flat' ? 'flat' : 'hisui';
    profileSelect.value = state.profile;
    state.routes = { ...DEFAULT_ROUTES[state.profile], ...(project.routes || {}) };
    updateRouteInputs();
    state.clsLayout = project.cls_layout === 'fullname' ? 'fullname' : 'package';
    clsSelect.value = state.clsLayout;
    if (project.project_name) {
      state.projectName = project.project_name;
      projectNameInput.value = project.project_name;
    }
    return true;
  }

  function invalidatePreview(message) {
    state.revision++;
    const hadPreview = Boolean(state.preview);
    state.preview = null;
    state.previewBlocked = false;
    for (const entry of state.sources) {
      if (entry.preview) {
        entry.preview = null;
        refreshEntry(entry);
      }
    }
    updateControls();
    if (hadPreview) setSummary(message || '配置已变化，请重新预览。', 'warn');
  }

  function sourceLabel(entry) {
    if (entry.pastedImage) return `粘贴图片 #${entry.number} · ${basename(entry.path)}`;
    return entry.kind === 'path'
      ? entry.path
      : `粘贴内容 #${entry.number}（${entry.text.length} 字符）`;
  }

  function refreshEntry(entry) {
    const ref = rowRefs.get(entry.id);
    if (!ref) return;
    const item = entry.preview || {};
    const kind = item.kind || (entry.role === 'attachment' ? 'attachment' : extensionKind(entry.filename || entry.destination || entry.path));
    ref.kind.textContent = KIND_TEXT[kind] || kind || '—';
    const status = item.status || item.final_status || '';
    ref.status.textContent = STATUS_TEXT[status] || status || '待预览';
    ref.status.className = `badge${status ? ` ${status}` : ''}`;
    const notes = [];
    if (item.destination) notes.push(`目标：${item.destination}`);
    if (item.filename) notes.push(`文件名：${item.filename}`);
    for (const note of item.notes || []) notes.push(String(note));
    if (item.error) notes.push(String(item.error));
    ref.notes.textContent = notes.join('；');
    ref.notes.classList.toggle('error', ['pending', 'invalid', 'conflict', 'failed', 'rollback-failed'].includes(status));
  }

  function renderSources() {
    clear(sourceList);
    clear($('attachment-list'));
    rowRefs.clear();
    for (const entry of state.sources) {
      const article = el('article', 'source-item');
      const head = el('div', 'source-head');
      const name = el('span', 'source-name', sourceLabel(entry));
      const kindBadge = el('span', 'badge', '—');
      const statusBadge = el('span', 'badge', '待预览');
      const remove = el('button', 'btn small danger', '删除');
      remove.type = 'button';
      remove.addEventListener('click', () => {
        if (state.busy) return;
        if (activeContentId === entry.id) $('content-preview').close();
        state.sources = state.sources.filter(item => item.id !== entry.id);
        rowRefs.delete(entry.id);
        invalidatePreview('来源清单已变化，请重新预览。');
        renderSources();
        setStatus(`已删除 1 项，当前 ${state.sources.length} 项。`);
      });
      head.append(name, kindBadge, statusBadge);
      const canPreviewImage = entry.kind === 'path' && (entry.imageUrl || IMAGE_NAME.test(entry.path));
      if (entry.kind === 'text' || canPreviewImage) {
        const inspect = el('button', 'btn small', entry.kind === 'text' ? '预览代码' : '预览图片');
        inspect.type = 'button';
        inspect.addEventListener('click', () => showContent(entry));
        head.appendChild(inspect);
      }
      head.appendChild(remove);

      const fields = el('div', 'source-fields');
      const filenameLabel = el('label', 'field');
      filenameLabel.appendChild(el('span', 'field-label', '文件名'));
      const filenameInput = document.createElement('input');
      filenameInput.type = 'text';
      filenameInput.spellcheck = false;
      filenameInput.value = entry.filename || '';
      filenameInput.placeholder = '留空自动识别';
      filenameInput.addEventListener('input', () => {
        entry.filename = filenameInput.value;
        invalidatePreview();
        refreshEntry(entry);
      });
      filenameLabel.appendChild(filenameInput);

      const destinationLabel = el('label', 'field');
      destinationLabel.appendChild(el('span', 'field-label', '目标相对路径'));
      const destinationInput = document.createElement('input');
      destinationInput.type = 'text';
      destinationInput.spellcheck = false;
      destinationInput.value = entry.destination || '';
      destinationInput.placeholder = '留空按目录规则生成';
      if (entry.role === 'attachment') {
        destinationInput.disabled = true;
        destinationInput.placeholder = '自动保存到本次需求目录的 attachments/';
      }
      destinationInput.addEventListener('input', () => {
        entry.destination = destinationInput.value;
        invalidatePreview();
        refreshEntry(entry);
      });
      destinationLabel.appendChild(destinationInput);
      fields.append(filenameLabel, destinationLabel);

      const notes = el('p', 'source-notes');
      article.append(head, fields, notes);
      if (entry.imageUrl) {
        const thumb = el('img', 'image-thumb');
        thumb.alt = '已粘贴图片缩略图';
        thumb.src = entry.imageUrl;
        thumb.addEventListener('click', () => showContent(entry));
        article.appendChild(thumb);
      }
      (entry.role === 'attachment' ? $('attachment-list') : sourceList).appendChild(article);
      rowRefs.set(entry.id, { kind: kindBadge, status: statusBadge, notes });
      refreshEntry(entry);
    }
    sourceEmpty.hidden = state.sources.length > 0;
    sourceCount.textContent = `${state.sources.length} 项`;
    updateControls();
  }

  function addPaths(paths, role = 'source', imageUrl = '') {
    let added = 0;
    for (const path of paths) {
      if (typeof path !== 'string' || !path.trim()) continue;
      state.sources.push({
        id: `src-${state.nextSourceId}`, number: state.nextSourceId++, kind: 'path',
        path: path.trim(), role, text: '', filename: '', destination: '', preview: null, imageUrl, pastedImage: !!imageUrl,
      });
      added++;
    }
    if (!added) return 0;
    invalidatePreview('来源清单已变化，请重新预览。');
    renderSources();
    return added;
  }

  function addText(text, filename = '') {
    if (typeof text !== 'string' || !text.trim()) return false;
    state.sources.push({
      id: `src-${state.nextSourceId}`, number: state.nextSourceId++, kind: 'text',
      path: '', text, filename: filename.trim(), destination: '', preview: null,
    });
    invalidatePreview('来源清单已变化，请重新预览。');
    renderSources();
    return true;
  }

  function buildRequest(empty = false) {
    if (!state.root.trim()) throw new Error('请先选择或输入项目目录。');
    const sources = (empty ? [] : state.sources).map(entry => {
      const spec = entry.kind === 'path' ? { path: entry.path } : { text: entry.text };
      spec.role = entry.role || 'source';
      if (entry.filename.trim()) spec.filename = entry.filename.trim();
      if (entry.destination.trim()) spec.destination = entry.destination.trim();
      return spec;
    });
    return {
      session_id: state.sessionId,
      root: state.root.trim(),
      project_name: state.projectName.trim(),
      profile: state.profile,
      routes: Object.fromEntries(KINDS.map(kind => [kind, state.routes[kind] ?? ''])),
      cls_layout: state.clsLayout,
      requirement: { ...state.requirement },
      sources,
      work_package: true,
    };
  }

  function planSummaryText(plan) {
    const summary = plan.summary || {};
    const parts = [`共 ${summary.total || 0} 项`, `可导入 ${summary.ready || 0}`];
    if (summary.same) parts.push(`内容一致 ${summary.same}`);
    if (summary.exists) parts.push(`同名文件 ${summary.exists}`);
    if (summary.pending) parts.push(`待补充 ${summary.pending}`);
    if (summary.invalid) parts.push(`无效 ${summary.invalid}`);
    if (summary.conflict) parts.push(`目标重复 ${summary.conflict}`);
    let text = `预览完成：${parts.join('，')}。`;
    if (summary.exists) text += ` 同名文件将按“${conflictSelect.value === 'backup-replace' ? '备份后替换' : '默认跳过'}”处理。`;
    if (!plan.root_exists) text += ' 项目目录将在导入时创建。';
    if (plan.work_package?.enabled) text += ` 需求目录：${plan.work_package.relative_dir}，包含阅读入口和修改说明。`;
    if ((summary.pending || 0) + (summary.invalid || 0) + (summary.conflict || 0) > 0) {
      text += ' 需要先修正待补充/无效/冲突项并重新预览，整批导入会被阻止。';
    }
    if (plan.warnings?.length) text += ` 警告：${plan.warnings.join('；')}`;
    return text;
  }

  function applyPlan(preview) {
    const plan = preview.plan || {};
    state.preview = preview;
    for (let index = 0; index < state.sources.length; index++) {
      state.sources[index].preview = plan.items?.[index] || null;
      refreshEntry(state.sources[index]);
    }
    const summary = plan.summary || {};
    state.previewBlocked = Boolean(plan.work_package?.exists) || (summary.pending || 0) + (summary.invalid || 0) + (summary.conflict || 0) > 0;
    setSummary(planSummaryText(plan), state.previewBlocked ? 'warn' : 'ok');
    if (!state.projectName.trim() && plan.project_name) {
      state.projectName = plan.project_name;
      projectNameInput.value = plan.project_name;
    }
    resultBox.hidden = true;
    updateControls();
  }

  async function saveSettings() {
    if (!state.root.trim()) return;
    try {
      const response = await hostCall('panel_save_settings', {
        root: state.root.trim(),
        project_name: state.projectName.trim(),
        profile: state.profile,
        routes: Object.fromEntries(KINDS.map(kind => [kind, (state.routes[kind] || '').trim()])),
        cls_layout: state.clsLayout,
      });
      if (response?.settings) {
        state.settings = response.settings;
        refreshRecentSelect();
      }
    } catch (error) {
      setStatus(`目录规则保存失败：${error.message}`, true);
    }
  }

  function addResultRow(label, value, { mono = false, error = false } = {}) {
    const row = el('div');
    if (label) {
      const strong = el('strong', '', `${label}：`);
      row.appendChild(strong);
    }
    const span = el('span', `${mono ? 'mono' : ''}${error ? ' error-text' : ''}`, value);
    row.appendChild(span);
    if (error) row.style.color = 'var(--negative)';
    resultBox.appendChild(row);
  }

  function showImportResult(payload) {
    clear(resultBox);
    resultBox.hidden = false;
    const ok = payload?.ok === true;
    addResultRow('', ok ? '导入完成' : '导入未完成');
    addResultRow('状态', payload?.status || '—');
    if (payload?.batch_id) addResultRow('批次', payload.batch_id, { mono: true });
    if (payload?.batch_dir) addResultRow('批次目录', payload.batch_dir, { mono: true });
    if (ok && payload?.work_package?.dir) {
      addResultRow('需求目录', payload.work_package.dir, { mono: true });
      addResultRow('交给 AI 的提示词', `请读取“${payload.work_package.dir}”中的 README.md、修改说明.md、attachments 当前材料和已有 handoffs 工作交接，复核材料变化及当前源码，接着实现需求并验证。完成后追加真实进度和下一步，保留原始修改说明。`, { mono: true });
    }
    const summary = payload?.summary || {};
    const counts = Object.entries(summary).filter(([, value]) => typeof value === 'number' && value > 0)
      .map(([key, value]) => `${STATUS_TEXT[key] || key} ${value}`);
    if (counts.length) addResultRow('统计', counts.join('，'));
    if (payload?.message) addResultRow('说明', payload.message);
    if (payload?.error?.message) addResultRow('错误', payload.error.message, { error: true });
    for (const item of payload?.blocked || []) {
      addResultRow(`阻塞 #${(item.index ?? 0) + 1}`, `${STATUS_TEXT[item.status] || item.status}${item.notes?.length ? `：${item.notes.join('；')}` : ''}`, { error: true });
    }
    for (const item of payload?.items || []) {
      const status = item.final_status || item.status;
      if (!['written', 'replaced', 'failed', 'rolled-back', 'rollback-failed', 'skipped-not-attempted'].includes(status) && !item.error) continue;
      addResultRow(`#${(item.index ?? 0) + 1}`, `${STATUS_TEXT[status] || status}${item.destination ? `：${item.destination}` : ''}${item.error ? `（${item.error}）` : ''}`, { error: Boolean(item.error) });
    }
  }

  async function runPreview() {
    let request;
    try {
      request = buildRequest();
    } catch (error) {
      setStatus(error.message, true);
      return;
    }
    state.busy = true;
    updateControls();
    setStatus('正在生成预览…');
    const revision = state.revision;
    try {
      const response = await hostCall('panel_preview', request);
      if (revision !== state.revision) {
        setSummary('预览期间配置已变化，请重新预览。', 'warn');
        setStatus('预览结果已作废。');
        return;
      }
      applyPlan(response);
      setStatus('预览完成；确认后点击“导入”。');
      void saveSettings();
    } catch (error) {
      setSummary(`预览失败：${error.message}`, 'error');
      setStatus(`预览失败：${error.message}`, true);
    } finally {
      state.busy = false;
      updateControls();
    }
  }

  async function runImport() {
    if (!state.preview) {
      setStatus('请先预览，确认目标路径和状态后再导入。', true);
      return;
    }
    const previewId = state.preview.preview_id;
    state.busy = true;
    updateControls();
    setStatus('正在导入…');
    try {
      const payload = await hostCall('panel_import', { preview_id: previewId, conflict: conflictSelect.value });
      state.preview = null;
      state.previewBlocked = false;
      for (let index = 0; index < state.sources.length; index++) {
        const item = payload?.items?.[index];
        if (item) {
          state.sources[index].preview = { ...(state.sources[index].preview || {}), ...item };
          refreshEntry(state.sources[index]);
        }
      }
      showImportResult(payload);
      if (payload?.ok === true) {
        const summary = payload.summary || {};
        const skipped = (summary['skipped-same'] || 0) + (summary['skipped-existing'] || 0);
        setSummary(`导入完成${payload.batch_id ? `，批次 ${payload.batch_id}` : ''}${skipped ? `，跳过 ${skipped} 项` : ''}。再次导入前请重新预览。`, 'ok');
        setStatus('导入完成。');
      } else {
        setStatus(payload?.error?.message || payload?.message || '导入未完成，请查看结果与批次目录。', true);
        setSummary('导入未完成，请检查结果；如需重试请重新预览。', 'warn');
      }
      void saveSettings();
    } catch (error) {
      state.preview = null;
      state.previewBlocked = false;
      for (const entry of state.sources) {
        if (entry.preview) {
          entry.preview = { ...entry.preview, status: 'pending' };
          refreshEntry(entry);
        }
      }
      setStatus(`导入失败：${error.message} 请重新预览后再导入。`, true);
      setSummary('导入了未完成或已被拒绝，请重新预览。', 'error');
    } finally {
      state.busy = false;
      updateControls();
    }
  }

  function applyDirectory(root, { rememberRule = true } = {}) {
    if (!root) return;
    clear($('requirement-results'));
    state.root = root;
    rootInput.value = root;
    if (!state.projectName.trim()) {
      state.projectName = basename(root);
      projectNameInput.value = state.projectName;
    }
    if (rememberRule) {
      const project = findProject(root);
      if (project) applyProjectRule(project);
    }
    recentSelect.value = (state.settings?.recent_roots || []).includes(root) ? root : '';
    invalidatePreview('项目目录已变化，请重新预览。');
  }

  rootInput.addEventListener('input', () => {
    state.root = rootInput.value;
    clear($('requirement-results'));
    recentSelect.value = '';
    invalidatePreview();
  });
  rootInput.addEventListener('change', () => {
    if (!state.projectName.trim() && state.root.trim()) {
      state.projectName = basename(state.root);
      projectNameInput.value = state.projectName;
      invalidatePreview();
    }
  });
  projectNameInput.addEventListener('input', () => {
    state.projectName = projectNameInput.value;
    invalidatePreview();
  });
  recentSelect.addEventListener('change', () => {
    if (!recentSelect.value) return;
    applyDirectory(recentSelect.value);
    setStatus(`已载入最近项目：${recentSelect.value}`);
  });
  profileSelect.addEventListener('change', () => {
    state.profile = profileSelect.value === 'flat' ? 'flat' : 'hisui';
    state.routes = { ...DEFAULT_ROUTES[state.profile] };
    updateRouteInputs();
    invalidatePreview('布局已切换，请重新预览。');
  });
  clsSelect.addEventListener('change', () => {
    state.clsLayout = clsSelect.value === 'fullname' ? 'fullname' : 'package';
    invalidatePreview('CLS 目录规则已修改，请重新预览。');
  });
  for (const kind of KINDS) {
    routeInputs[kind].addEventListener('input', () => {
      state.routes[kind] = routeInputs[kind].value;
      invalidatePreview('目录规则已修改，请重新预览。');
    });
  }
  for (const [name, input] of Object.entries(requirementInputs)) {
    input.addEventListener('input', () => {
      state.requirement[name] = input.value;
      invalidatePreview();
    });
  }

  $('choose-directory').addEventListener('click', async () => {
    if (state.busy) return;
    state.busy = true;
    updateControls();
    try {
      const value = await hostCall('panel_choose_directory', { initial: state.root.trim() });
      if (value?.canceled || !value?.path) {
        setStatus('已取消目录选择，未做任何更改。');
        return;
      }
      applyDirectory(value.path);
      setStatus(`已选择目录：${value.path}`);
    } catch (error) {
      setStatus(`选择目录失败：${error.message}`, true);
    } finally {
      state.busy = false;
      updateControls();
    }
  });

  $('choose-files').addEventListener('click', async () => {
    if (state.busy) return;
    state.busy = true;
    updateControls();
    try {
      const value = await hostCall('panel_choose_files', { initial: state.root.trim() });
      if (value?.canceled || !value?.paths?.length) {
        setStatus('已取消文件选择，未添加任何来源。');
        return;
      }
      const added = addPaths(value.paths);
      setStatus(`已添加 ${added} 个文件来源，共 ${state.sources.length} 项。`);
    } catch (error) {
      setStatus(`选择文件失败：${error.message}`, true);
    } finally {
      state.busy = false;
      updateControls();
    }
  });

  $('collect-clipboard').addEventListener('click', async () => {
    if (state.busy) return;
    state.busy = true;
    updateControls();
    try {
      const value = await hostCall('panel_clipboard');
      if (value?.kind === 'files' && value.files?.length) {
        const added = addPaths(value.files);
        setStatus(`已从剪贴板收集 ${added} 个文件来源。${value.note || ''}`);
      } else if (value?.kind === 'text' && typeof value.text === 'string' && value.text.trim()) {
        addText(value.text);
        setStatus(`已从剪贴板收集文本，可在清单中补充文件名。${value.note || ''}`);
      } else {
        setStatus(value?.note || '剪贴板中没有可用的文件列表或文本。');
      }
    } catch (error) {
      setStatus(`读取剪贴板失败：${error.message}`, true);
    } finally {
      state.busy = false;
      updateControls();
    }
  });

  $('paste-code').addEventListener('click', () => {
    if (state.busy) return;
    pasteBox.hidden = !pasteBox.hidden;
    if (!pasteBox.hidden) pasteText.focus();
  });
  $('close-content-preview').addEventListener('click', () => $('content-preview').close());
  $('content-preview').addEventListener('close', () => {
    if ($('content-preview').open) return;
    contentPreviewRevision++;
    activeContentId = null;
    $('content-code').textContent = '';
    $('content-image').removeAttribute('src');
    $('content-image').onload = null;
    $('content-image').onerror = null;
  });
  $('image-size').addEventListener('click', () => {
    const original = $('content-image').classList.toggle('original');
    $('image-size').textContent = original ? '适应窗口' : '原始尺寸';
  });
  pasteText.addEventListener('paste', event => {
    const items = Array.from(event.clipboardData?.items || []);
    let images = items.filter(item => item.kind === 'file' && item.type.startsWith('image/')).map(item => item.getAsFile()).filter(Boolean);
    if (!images.length) images = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith('image/'));
    if (!images.length) return; // Leave ordinary text paste to the browser.
    event.preventDefault();
    if (!state.ready || state.busy && !processingImagePaste) { setStatus('当前操作尚未完成，请稍后重新粘贴图片。', true); return; }
    if (images.length > 20 || state.sources.length + queuedImages + images.length > 200) { setStatus('一次最多粘贴 20 张图片，清单最多 200 项。', true); return; }
    queuedImages += images.length;
    imagePasteQueue.push(images);
    void processImagePastes();
  });

  async function processImagePastes() {
    if (processingImagePaste) return;
    processingImagePaste = true;
    state.busy = true;
    updateControls();
    let added = 0;
    const failures = [];
    try {
      while (imagePasteQueue.length) for (const file of imagePasteQueue.shift()) {
        try {
          if (file.size > 20 * 1024 * 1024) throw new Error('图片超过 20 MiB，请选择文件导入。');
          const url = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result);
            reader.onerror = () => reject(new Error('无法读取本次粘贴的图片。'));
            reader.readAsDataURL(file);
          });
          const value = await hostCall('panel_add_image', { image_base64: url.slice(url.indexOf(',') + 1), mime_type: file.type });
          if (!value?.path) throw new Error('图片未暂存成功。');
          added += addPaths([value.path], 'attachment', url);
        } catch (error) { failures.push(error.message); }
        finally { queuedImages--; }
      }
      setStatus(`已将 ${added} 张图片加入需求附件，已有代码输入保持不变。${failures.length ? ` ${failures.length} 张未添加：${failures.join('；')}` : ''}`, !!failures.length);
    } finally { state.busy = false; processingImagePaste = false; updateControls(); }
  }
  $('cancel-paste').addEventListener('click', () => {
    pasteBox.hidden = true;
    setStatus('已取消粘贴，未添加内容。');
  });
  $('add-paste').addEventListener('click', () => {
    if (state.busy) return;
    const text = pasteText.value;
    if (!text.trim()) {
      setStatus('粘贴内容为空，未添加。', true);
      return;
    }
    const added = addText(text, pasteFilename.value);
    if (added) {
      pasteText.value = '';
      pasteFilename.value = '';
      setStatus(`已追加粘贴内容，共 ${state.sources.length} 项。`);
    }
  });

  previewButton.addEventListener('click', runPreview);
  importButton.addEventListener('click', runImport);

  async function collectAttachments(mode) {
    if (state.busy) return;
    state.busy = true;
    updateControls();
    try {
      const value = await hostCall(mode === 'files' ? 'panel_choose_files' : mode === 'screenshot' ? 'panel_screenshot' : 'panel_clipboard', mode === 'files' ? { initial: state.root.trim(), purpose: 'attachment' } : {});
      const paths = mode === 'files' ? value.paths : mode === 'screenshot' ? (value.path ? [value.path] : []) : value.files;
      if (paths?.length) setStatus(`已收集 ${addPaths(paths, 'attachment')} 个需求附件。`);
      else setStatus(value.note || '未收集到附件。请复制文件、复制截图或选择已保存的文件。');
    } catch (error) { setStatus(error.message, true); }
    finally { state.busy = false; updateControls(); }
  }
  $('choose-attachments').addEventListener('click', () => collectAttachments('files'));
  $('attachment-clipboard').addEventListener('click', () => collectAttachments('clipboard'));
  $('capture-screenshot').addEventListener('click', () => collectAttachments('screenshot'));
  $('search-requirements').addEventListener('click', async () => {
    if (state.busy) return;
    if (!state.root.trim()) { setStatus('请先选择或输入项目目录。', true); return; }
    state.busy = true; updateControls();
    const output = $('requirement-results');
    clear(output);
    const searchedRoot = state.root.trim();
    const searchedQuery = $('requirement-search').value;
    try {
      const response = await hostCall('panel_list_requirements', { root: searchedRoot, query: searchedQuery });
      if (searchedRoot !== state.root.trim() || searchedQuery !== $('requirement-search').value) {
        setStatus('项目目录或查询已变化，请重新查找。'); return;
      }
      if (!response.items?.length) output.appendChild(el('p', 'hint', '没有匹配的需求目录。'));
      for (const item of response.items || []) {
        const row = el('article', 'source-item');
        row.appendChild(el('strong', '', `${item.id || '未编号'} · ${item.title || '未填写标题'}`));
        row.appendChild(el('p', 'source-notes', item.directory));
        row.appendChild(el('p', 'hint', `请读取“${item.directory}”中的 README.md、修改说明.md、attachments 当前材料和已有 handoffs 工作交接，复核材料变化及源码后继续处理；结束时追加进度和下一步。`));
        if (item.warnings?.length) row.appendChild(el('p', 'source-notes error', item.warnings.join('；')));
        output.appendChild(row);
      }
      for (const warning of response.warnings || []) output.appendChild(el('p', 'source-notes', warning));
      setStatus(`找到 ${response.matches || 0} 个需求；材料归档状态不代表业务需求已实现。`);
    } catch (error) { setStatus(`查找需求失败：${error.message}`, true); }
    finally { state.busy = false; updateControls(); }
  });
  $('create-empty').addEventListener('click', async () => {
    if (state.busy) return;
    let request;
    try { request = buildRequest(true); } catch (error) { setStatus(error.message, true); return; }
    invalidatePreview();
    state.busy = true;
    updateControls();
    try {
      const preview = await hostCall('panel_preview', request);
      const payload = await hostCall('panel_import', { preview_id: preview.preview_id, conflict: 'skip' });
      showImportResult(payload);
      setStatus(payload.ok ? '空白需求目录已创建，可自行放入材料；当前清单尚未导入。' : '需求目录创建失败。', !payload.ok);
      void saveSettings();
    } catch (error) { setStatus(error.message, true); }
    finally { state.busy = false; updateControls(); }
  });

  async function main() {
    setConnection('', '连接中');
    setSummary('尚未预览。');
    updateRouteInputs();
    updateControls();
    if (window.sourceIntakeHost?.onClose) {
      window.sourceIntakeHost.onClose(() => {
        setConnection('error', '已关闭');
        state.ready = false;
        updateControls();
        setStatus('面板已关闭。', true);
      });
    }
    try {
      const data = await hostCall('panel_state');
      state.ready = true;
      applySettings(data);
      applyProjectRule(findProject(rootInput.value));
      updateControls();
      setConnection('online', '已连接');
      const warnings = state.settings?.warnings || [];
      if (warnings.length) setStatus(warnings.join('；'));
      else setStatus('请选择项目目录并添加来源；预览不会写入文件。');
    } catch (error) {
      setConnection('error', '未连接');
      setStatus(`连接失败：${error.message}`, true);
      setSummary('无法连接 Codex 面板宿主。', 'error');
    }
  }

  main();
})();
