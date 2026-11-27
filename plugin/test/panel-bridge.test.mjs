import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const tick = () => new Promise(resolve => setImmediate(resolve));

test('panel bridge negotiates MCP Apps 2026-01-26 and only trusts host messages', async () => {
  const source = await readFile(new URL('../panel-bridge.js', import.meta.url), 'utf8');
  const sent = [];
  let receive;
  const parent = { postMessage(message) { sent.push(message); } };
  const window = {
    parent,
    addEventListener(name, callback) { if (name === 'message') receive = callback; },
  };
  const document = { documentElement: { style: {} } };
  vm.runInNewContext(source, { window, document, setTimeout, clearTimeout });

  const init = sent.find(message => message.method === 'ui/initialize');
  assert.ok(init);
  assert.equal(init.params.appInfo.name, 'source-intake-panel');
  assert.equal(init.params.protocolVersion, '2026-01-26');
  receive({
    source: parent,
    data: { jsonrpc: '2.0', id: init.id, result: { protocolVersion: '2026-01-26', hostContext: { theme: 'dark' } } },
  });
  await tick();
  assert.equal(document.documentElement.style.colorScheme, 'dark');
  assert.ok(sent.some(message => message.method === 'ui/notifications/initialized'));

  const request = window.sourceIntakeHost.call('panel_state');
  await tick();
  const toolCall = sent.find(message => message.method === 'tools/call');
  assert.equal(toolCall.params.name, 'panel_state');
  let resolved = false;
  request.then(() => { resolved = true; });
  receive({ source: {}, data: { jsonrpc: '2.0', id: toolCall.id, result: { structuredContent: { forged: true } } } });
  await tick();
  assert.equal(resolved, false, 'messages from other frames are ignored');
  receive({
    source: parent,
    data: { jsonrpc: '2.0', id: toolCall.id, result: { structuredContent: { preview_id: 'real' }, content: [] } },
  });
  assert.deepEqual(await request, { preview_id: 'real' });

  const failing = window.sourceIntakeHost.call('panel_import');
  await tick();
  const failingCall = sent.filter(message => message.method === 'tools/call').at(-1);
  receive({ source: parent, data: { jsonrpc: '2.0', id: failingCall.id, error: { message: '内核拒绝' } } });
  await assert.rejects(failing, /内核拒绝/);

  const waiting = window.sourceIntakeHost.call('panel_preview');
  await tick();
  receive({ source: parent, data: { jsonrpc: '2.0', id: 999, method: 'ui/resource-teardown' } });
  await assert.rejects(waiting, /已关闭/);
  await assert.rejects(window.sourceIntakeHost.call('panel_state'), /已关闭/);
  receive({ source: parent, data: { jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { theme: 'light' } } });
  assert.equal(document.documentElement.style.colorScheme, 'light');

  const bridgeSource = source;
  assert.doesNotMatch(bridgeSource, /navigator\.clipboard/);
  assert.doesNotMatch(bridgeSource, /localStorage/);
});

test('panel bridge gives BOS query 40s, keeps status fast and distinguishes timeout from closure', async () => {
  const source = await readFile(new URL('../panel-bridge.js', import.meta.url), 'utf8');
  const sent = [];
  const timers = new Map();
  let nextTimer = 1;
  let receive;
  const parent = { postMessage(message) { sent.push(message); } };
  const window = {
    parent,
    addEventListener(name, callback) { if (name === 'message') receive = callback; },
  };
  const document = { documentElement: { style: {} } };
  vm.runInNewContext(source, {
    window, document,
    setTimeout(callback, timeoutMs) { const id = nextTimer++; timers.set(id, { callback, timeoutMs }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  const init = sent.find(message => message.method === 'ui/initialize');
  receive({ source: parent, data: { jsonrpc: '2.0', id: init.id, result: { protocolVersion: '2026-01-26' } } });
  await tick();

  const status = window.sourceIntakeHost.call('panel_bos_status');
  await tick();
  const statusTimer = [...timers.values()].find(timer => timer.timeoutMs === 10000);
  assert.ok(statusTimer);
  statusTimer.callback();
  await assert.rejects(status, error => error.code === 'ETIMEDOUT' && /可能仍在执行/.test(error.message));

  const query = window.sourceIntakeHost.call('panel_bos_query', { id: '1001' });
  await tick();
  const queryCall = sent.filter(message => message.method === 'tools/call').at(-1);
  assert.equal(queryCall.params.name, 'panel_bos_query');
  const queryTimer = [...timers.values()].find(timer => timer.timeoutMs === 40000);
  assert.ok(queryTimer);
  queryTimer.callback();
  await assert.rejects(query, error => error.code === 'ETIMEDOUT');
  receive({ source: parent, data: { jsonrpc: '2.0', id: queryCall.id, result: { structuredContent: { count: 1 } } } });

  const waiting = window.sourceIntakeHost.call('panel_preview');
  await tick();
  receive({ source: parent, data: { jsonrpc: '2.0', id: 999, method: 'ui/resource-teardown' } });
  await assert.rejects(waiting, error => error.code === 'ECLOSED' && /已关闭/.test(error.message));
});
