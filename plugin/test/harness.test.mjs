import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { startHarness } from '../harness.mjs';
import { PYTHON } from './helpers.mjs';

test('local panel harness embeds the real panel, injects native demo data and confines writes to temp', async t => {
  const harness = await startHarness({ python: PYTHON });
  t.after(() => harness.close());
  const rpc = async (name, args = {}) => {
    const response = await fetch(new URL('/rpc', harness.url), {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-source-intake-harness': harness.token },
      body: JSON.stringify({ name, arguments: args }),
    });
    const body = await response.json();
    assert.equal(response.ok, true, body.error);
    assert.equal(body.ok, true);
    return body.result.structuredContent;
  };

  const hostPage = await (await fetch(harness.url)).text();
  assert.match(hostPage, /源码导入 · 本地验收 Harness/);
  assert.match(hostPage, /src="\/panel"/);
  const panel = await (await fetch(new URL('/panel', harness.url))).text();
  assert.match(panel, /ui\/initialize/);
  assert.match(panel, /源码导入/);
  assert.doesNotMatch(panel, /<script src="\/panel\.js"/);
  assert.match(panel, /textContent/);

  const denied = await fetch(new URL('/rpc', harness.url), { method: 'POST', body: '{}' });
  assert.equal(denied.status, 403);

  const state = await rpc('panel_state');
  assert.ok(Array.isArray(state.settings.recent_roots));
  assert.deepEqual(await rpc('panel_bos_status'), { configured: true, connected: false });
  const bos = await rpc('panel_bos_query', { id: '1001', limit: 1 });
  assert.equal(bos.count, 1);
  assert.equal(bos.items[0]['需求号'], 1001);
  assert.match(bos.items[0]['标题'], /合成 BOS 需求/);
  assert.deepEqual(await rpc('panel_bos_status'), { configured: true, connected: true });
  const directory = await rpc('panel_choose_directory');
  assert.equal(directory.path, harness.demo.projectRoot);
  await assert.rejects(access(harness.demo.projectRoot), { code: 'ENOENT' }, 'the demo picker does not create the directory');
  const clipboard = await rpc('panel_clipboard');
  assert.equal(clipboard.kind, 'files');
  assert.equal(clipboard.files.length, 3);

  const preview = await rpc('panel_preview', {
    session_id: 'harness-demo-session',
    root: harness.demo.projectRoot,
    sources: [{ path: harness.demo.files.js }],
  });
  assert.equal(preview.plan.items[0].status, 'ready');
  const imported = await rpc('panel_import', { preview_id: preview.preview_id, conflict: 'skip' });
  assert.equal(imported.ok, true);
  const written = join(harness.demo.projectRoot, 'frontend/scripts/dhcnewpro/dhcemc/demo.js');
  assert.deepEqual(await readFile(written), await readFile(harness.demo.files.js));

  const outside = await fetch(new URL('/rpc', harness.url), {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-source-intake-harness': harness.token },
    body: JSON.stringify({
      name: 'panel_preview',
      arguments: { root: join(harness.root, '..'), sources: [{ text: 'var x=1;', filename: 'x.js' }] },
    }),
  });
  assert.equal(outside.status, 400);

  const root = harness.root;
  await harness.close();
  await assert.rejects(access(root), { code: 'ENOENT' }, 'closing the harness removes only its temp directory');
});

test('local harness accepts an injected synthetic BOS client', async t => {
  const calls = [];
  const bos = {
    status: () => ({ configured: false, connected: false }),
    async query(args) { calls.push(args); return { items: [], count: 0, total: 0, truncated: false }; },
    async close() {},
  };
  const harness = await startHarness({ python: PYTHON, bos });
  t.after(() => harness.close());
  const response = await fetch(new URL('/rpc', harness.url), {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-source-intake-harness': harness.token },
    body: JSON.stringify({ name: 'panel_bos_query', arguments: { id: '2001' } }),
  });
  const body = await response.json();
  assert.equal(response.ok, true, body.error);
  assert.equal(body.result.structuredContent.count, 0);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].id, '2001');
});
