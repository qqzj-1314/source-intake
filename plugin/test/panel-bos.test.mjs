import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import vm from 'node:vm';
import { normalizeBosRequirement } from '../bos-client.mjs';
import { createNative, fixture } from './helpers.mjs';

class FakeElement {
  constructor(tagName = 'div', id = '') {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.childNodes = this.children;
    this.attributes = {};
    this.listeners = new Map();
    this.style = {};
    this.value = '';
    this.className = '';
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this._text = '';
    const classes = new Set();
    this.classList = {
      add: (...names) => { names.forEach(name => classes.add(name)); this.className = [...classes].join(' '); },
      remove: (...names) => { names.forEach(name => classes.delete(name)); this.className = [...classes].join(' '); },
      toggle: (name, force) => {
        const enabled = force ?? !classes.has(name);
        if (enabled) classes.add(name); else classes.delete(name);
        this.className = [...classes].join(' ');
        return enabled;
      },
      contains: name => classes.has(name),
    };
  }
  get firstChild() { return this.children[0] || null; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; this.childNodes = this.children; }
  appendChild(child) { this._text = ''; this.children.push(child); return child; }
  append(...children) { for (const child of children) this.appendChild(child); }
  removeChild(child) { this.children = this.children.filter(item => item !== child); this.childNodes = this.children; return child; }
  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) || [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }
  async emit(name) {
    const results = (this.listeners.get(name) || []).map(callback => callback({ target: this, currentTarget: this }));
    return Promise.all(results);
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; void this.emit('close'); }
}

async function waitFor(check, message = 'condition') {
  for (let index = 0; index < 100; index++) {
    if (check()) return;
    await new Promise(resolve => setImmediate(resolve));
  }
  throw new Error(`Timed out waiting for ${message}`);
}

function syntheticRecord(id = 1001) {
  return {
    '需求号': id,
    '标题': `合成标题 ${id} <script>globalThis.__bosExecuted=true</script>`,
    '描述': `合成描述 ${id} <img src=x onerror="globalThis.__bosExecuted=true">`,
    '操作时间': '2026-01-02 03:04:05',
    '创建时间': '2026-01-01 01:02:03',
    '状态': '合成状态',
    '类型': '合成类型',
    '来源': '合成来源',
    '发起组': '合成发起组',
    '发起人': '合成发起人',
    '接受组': '合成接受组',
    '接受人': '合成接受人',
    '当前组': '合成当前组',
    '当前人': '合成当前人',
    '产品': '合成产品',
    '产品名': '合成产品名',
    '模块': '合成模块',
    '附件': [],
  };
}

async function createPanel(t, bos) {
  let current;
  const pendingCalls = new Set();
  const native = createNative({
    chooseFiles(initial, purpose) {
      return { ok: true, canceled: false, paths: [join(current.root, purpose === 'attachment' ? 'synthetic.txt' : 'synthetic.js')] };
    },
  });
  current = await fixture(t, {
    native,
    server: { bos },
    async beforeCleanup() {
      // Imports save preferences in the background. Finish those writes before
      // fixture teardown closes the MCP connection and removes its directory.
      while (pendingCalls.size) await Promise.allSettled([...pendingCalls]);
    },
  });
  await writeFile(join(current.root, 'synthetic.js'), 'const synthetic = true;\n');
  await writeFile(join(current.root, 'synthetic.txt'), 'synthetic attachment\n');
  const html = await readFile(new URL('../panel.html', import.meta.url), 'utf8');
  const source = await readFile(new URL('../panel.js', import.meta.url), 'utf8');
  const elements = new Map();
  const created = [];
  for (const match of html.matchAll(/id="([^"]+)"/g)) {
    const id = match[1];
    elements.set(id, new FakeElement('div', id));
  }
  elements.get('conflict').value = 'skip';
  const document = {
    getElementById(id) { return elements.get(id) || null; },
    createElement(tagName) {
      const element = new FakeElement(tagName);
      created.push(element);
      return element;
    },
  };
  const calls = [];
  const closeCallbacks = [];
  const window = {
    sourceIntakeHost: {
      async call(name, args = {}) {
        const entry = { name, args: JSON.parse(JSON.stringify(args)), response: null };
        calls.push(entry);
        const request = current.call(name, args);
        pendingCalls.add(request);
        try {
          const response = await request;
          if (response.isError) throw new Error(response.content?.[0]?.text || '合成工具错误');
          entry.response = response.structuredContent;
          return response.structuredContent;
        } finally {
          pendingCalls.delete(request);
        }
      },
      onClose(callback) { closeCallbacks.push(callback); },
    },
  };
  vm.runInNewContext(`${normalizeBosRequirement.toString()}\n${source}`, {
    window, document, Intl, Date, Math, JSON, console, setTimeout, clearTimeout,
  });
  await waitFor(() => elements.get('connection-label').textContent === '已连接', 'panel initialization');
  await waitFor(() => elements.get('bos-connection').textContent !== '正在读取配置状态…', 'BOS status');
  return {
    fixture: current,
    elements,
    calls,
    created,
    waitFor,
    async input(id, value) { const element = elements.get(id); element.value = value; await element.emit('input'); },
    async click(id) { await elements.get(id).emit('click'); },
  };
}

const bosResult = items => ({ items, count: items.length, total: items.length, truncated: false });
const idleBos = query => ({ status: () => ({ configured: true, connected: false }), query, close: async () => {} });

test('BOS query, selection and explicit apply preserve materials and persist system/updated_at', async t => {
  const queries = [];
  const panel = await createPanel(t, idleBos(async args => {
    queries.push(args);
    return bosResult(args.id === '1001' ? [syntheticRecord()] : []);
  }));
  const { elements, fixture } = panel;
  const prompt = '保留合成提示 <b>不执行</b>\n第二行保持原文';
  await panel.input('root', join(fixture.root, 'project'));
  await panel.input('requirement-id', 'MANUAL-1');
  await panel.input('requirement-title', '手工标题');
  await panel.input('requirement-description', '手工描述');
  await panel.input('requirement-url', 'https://example.invalid/manual');
  await panel.input('requirement-prompt', prompt);
  await panel.click('choose-files');
  await panel.click('choose-attachments');
  assert.equal(elements.get('source-list').children.length, 1);
  assert.equal(elements.get('attachment-list').children.length, 1);

  await panel.click('preview');
  assert.equal(elements.get('import').disabled, false);
  assert.equal(panel.calls.filter(call => call.name === 'panel_preview').at(-1).args.requirement.system, 'manual');
  await panel.input('bos-id', '1001');
  await panel.click('bos-query');
  assert.equal(elements.get('requirement-id').value, 'MANUAL-1');
  assert.equal(elements.get('requirement-prompt').value, prompt);
  assert.equal(elements.get('import').disabled, false, 'query alone does not invalidate the preview');
  assert.match(elements.get('bos-results').textContent, /1001/);
  assert.match(elements.get('bos-results').textContent, /<script>/);

  const radio = elements.get('bos-results').children[0].children[0].children[0];
  await radio.emit('change');
  assert.equal(elements.get('requirement-id').value, 'MANUAL-1');
  assert.equal(elements.get('requirement-prompt').value, prompt);
  assert.equal(elements.get('import').disabled, false, 'selection alone does not invalidate the preview');
  assert.equal(elements.get('bos-apply').disabled, false);
  await panel.click('bos-apply');
  assert.equal(elements.get('requirement-id').value, '1001');
  assert.match(elements.get('requirement-title').value, /<script>/);
  assert.match(elements.get('requirement-description').value, /<img/);
  assert.equal(elements.get('requirement-url').value, '');
  assert.equal(elements.get('requirement-prompt').value, prompt);
  assert.equal(elements.get('requirement-source').textContent, 'BOS · 1001');
  assert.equal(elements.get('import').disabled, true, 'explicit apply invalidates the old preview');
  assert.match(elements.get('preview-summary').textContent, /原预览已失效/);
  assert.equal(elements.get('source-list').children.length, 1);
  assert.equal(elements.get('attachment-list').children.length, 1);
  assert.equal(panel.created.filter(element => element.tagName === 'SCRIPT').length, 0);

  await panel.click('preview');
  const request = panel.calls.filter(call => call.name === 'panel_preview').at(-1).args;
  assert.equal(request.requirement.system, 'BOS');
  assert.equal(request.requirement.updated_at, '2026-01-02 03:04:05');
  assert.equal(request.requirement.prompt, prompt);
  assert.equal(request.sources.length, 2);
  await panel.click('import');
  const imported = panel.calls.filter(call => call.name === 'panel_import').at(-1).response;
  assert.ok(imported, elements.get('status').textContent);
  assert.equal(imported.ok, true);
  const requirement = JSON.parse(await readFile(join(imported.batch_dir, 'requirement.json'), 'utf8'));
  assert.equal(requirement.system, 'BOS');
  assert.equal(requirement.id, '1001');
  assert.equal(requirement.updated_at, '2026-01-02 03:04:05');
  assert.equal(requirement.prompt, prompt);
  assert.equal(await readFile(join(imported.work_package.dir, '修改说明.md'), 'utf8'), prompt);

  await panel.click('switch-manual');
  assert.equal(elements.get('requirement-source').textContent, '手工填写');
  assert.equal(elements.get('requirement-prompt').value, prompt);
  await panel.click('bos-apply');
  assert.equal(elements.get('requirement-source').textContent, 'BOS · 1001');
  await panel.input('requirement-id', 'MANUAL-CHANGED');
  assert.equal(elements.get('requirement-source').textContent, '手工填写');
  await panel.click('preview');
  const changed = panel.calls.filter(call => call.name === 'panel_preview').at(-1).args.requirement;
  assert.equal(changed.system, 'manual');
  assert.equal(changed.updated_at, '');
  assert.equal(changed.prompt, prompt);
  assert.equal(queries.length, 1);
});

test('BOS advanced filters require a person/group and send explicit historical dates', async t => {
  const queries = [];
  const panel = await createPanel(t, idleBos(async args => { queries.push(args); return bosResult([]); }));
  await panel.input('bos-title', '合成历史标题');
  await panel.click('bos-query');
  assert.match(panel.elements.get('bos-query-status').textContent, /至少填写接受人或接受组/);
  assert.equal(queries.length, 0);
  await panel.input('bos-tuser', '合成接受人');
  await panel.input('bos-tgroup', '合成接受组');
  await panel.input('bos-date', '2025-02-01');
  await panel.input('bos-date2', '2025-01-01');
  await panel.click('bos-query');
  assert.match(panel.elements.get('bos-query-status').textContent, /开始日期不晚于截止日期/);
  assert.equal(queries.length, 0);
  await panel.input('bos-title', '合成历史标题');
  await panel.input('bos-date', '2025-01-01');
  await panel.input('bos-date2', '2025-01-03');
  await panel.click('bos-query');
  assert.equal(queries.length, 1);
  assert.equal(queries[0].tuser, '合成接受人');
  assert.equal(queries[0].tgroup, '合成接受组');
  assert.equal(queries[0].title, '合成历史标题');
  assert.equal(queries[0].date, '2025-01-01');
  assert.equal(queries[0].date2, '2025-01-03');
  assert.equal(queries[0].datetype, '创建时间');
  assert.equal(queries[0].explain, '源码导入插件查询需求');
});

test('BOS empty, error, missing fields, cancellation and late responses never overwrite newer state', async t => {
  const deferred = new Map();
  const panel = await createPanel(t, idleBos(args => {
    if (args.id === '400') throw new Error('合成查询失败');
    if (args.id === '200') return Promise.resolve(bosResult([]));
    if (args.id === '300') return Promise.resolve(bosResult([{ '需求号': 300, '标题': '合成缺字段', '状态': '合成状态' }]));
    if (args.id === '600') return Promise.resolve({ ...bosResult([syntheticRecord(600)]), total: 25, truncated: true });
    return new Promise(resolve => deferred.set(args.id, resolve));
  }));
  const { elements } = panel;
  await panel.input('requirement-id', 'MANUAL-KEEP');
  await panel.input('requirement-prompt', '合成提示保持');

  await panel.input('bos-id', '400');
  await panel.click('bos-query');
  assert.match(elements.get('bos-query-status').textContent, /合成查询失败/);
  assert.equal(elements.get('requirement-id').value, 'MANUAL-KEEP');
  await panel.input('bos-id', '200');
  await panel.click('bos-query');
  assert.match(elements.get('bos-query-status').textContent, /0 条/);
  assert.match(elements.get('bos-results').textContent, /没有匹配记录/);
  await panel.input('bos-id', '300');
  await panel.click('bos-query');
  assert.match(elements.get('bos-results').textContent, /核心字段无效：描述/);
  assert.equal(elements.get('bos-apply').disabled, true);
  assert.equal(elements.get('requirement-id').value, 'MANUAL-KEEP');
  await panel.input('bos-id', '600');
  await panel.click('bos-query');
  assert.match(elements.get('bos-query-status').textContent, /结果已截断/);

  await panel.input('bos-id', '500');
  const first = panel.click('bos-query');
  await waitFor(() => deferred.has('500'), 'first delayed query');
  assert.match(elements.get('bos-query-status').textContent, /正在查询 BOS/);
  await panel.input('bos-id', '501');
  const second = panel.click('bos-query');
  await waitFor(() => deferred.has('501'), 'second delayed query');
  deferred.get('501')(bosResult([syntheticRecord(501)]));
  await second;
  deferred.get('500')(bosResult([syntheticRecord(500)]));
  await first;
  assert.match(elements.get('bos-results').textContent, /501/);
  assert.doesNotMatch(elements.get('bos-results').textContent, /500/);
  assert.equal(elements.get('bos-id').value, '501');

  await panel.input('bos-id', '502');
  const cancelled = panel.click('bos-query');
  await waitFor(() => deferred.has('502'), 'cancelled query');
  await panel.click('bos-cancel');
  deferred.get('502')(bosResult([syntheticRecord(502)]));
  await cancelled;
  assert.match(elements.get('bos-query-status').textContent, /未中断服务端或上游查询/);
  assert.equal(elements.get('bos-results').children.length, 0);
  assert.equal(elements.get('requirement-id').value, 'MANUAL-KEEP');
  assert.equal(elements.get('requirement-prompt').value, '合成提示保持');
});
