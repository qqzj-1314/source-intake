import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { detectPython } from '../engine.mjs';

// Independent black-box checks: only public MCP messages and filesystem outcomes.
test('independent MCP plugin acceptance using the real stdio server', async t => {
  const plugin = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const temp = await mkdtemp(join(tmpdir(), 'source-intake-mcp-independent-'));
  const python = process.env.SOURCE_INTAKE_TEST_PYTHON || await detectPython();
  const transport = new StdioClientTransport({
    command: process.execPath, args: [join(plugin, 'server.mjs')], stderr: 'pipe',
    env: { ...process.env, SOURCE_INTAKE_PYTHON: python, LOCALAPPDATA: join(temp, 'preferences') },
  });
  let diagnostics = '';
  transport.stderr?.on('data', data => { diagnostics = (diagnostics + data.toString()).slice(-4000); });
  const client = new Client({ name: 'independent-source-intake-qa', version: '1.0.0' }, { capabilities: {} });
  const value = response => response.structuredContent ?? JSON.parse(response.content.find(c => c.type === 'text').text);
  const call = async (name, args = {}) => {
    const response = await client.callTool({ name, arguments: args });
    assert.ok(!response.isError, `${name}: ${response.content?.map(c => c.text || '').join('\n')} ${diagnostics}`);
    return value(response);
  };
  const rejected = async (name, args) => {
    try { return Boolean((await client.callTool({ name, arguments: args })).isError); }
    catch { return true; }
  };
  const exists = async path => { try { await readFile(path); return true; } catch { return false; } };
  const preview = (root, sources, session = 'independent-session') => call('panel_preview', {
    root, sources, profile: 'hisui', session_id: session,
    requirement: { id: 'BOS-PANEL-QA', title: '面板导入验收', description: '保留原始源码再修改需求' },
  });
  try {
    await client.connect(transport);
    await t.test('entrypoint metadata and app-only actions match the embedded-app protocol', async () => {
      const { tools } = await client.listTools();
      const open = tools.find(item => item.name === 'open_panel');
      assert.ok(open);
      assert.equal(open.annotations.readOnlyHint, true);
      assert.equal(open._meta.ui.resourceUri, 'ui://source-intake/panel/v1');
      assert.ok(open._meta['openai/ui'].entrypoints.some(item => item.type === 'global'));
      for (const name of ['panel_state','panel_clipboard','panel_choose_files','panel_choose_directory','panel_preview','panel_import','panel_save_settings']) {
        const tool = tools.find(item => item.name === name);
        assert.ok(tool, name);
        assert.deepEqual(tool._meta.ui.visibility, ['app'], name);
      }
      const resources = await client.listResources();
      assert.ok(resources.resources.some(item => item.uri === 'ui://source-intake/panel/v1'));
      const resource = await client.readResource({ uri: 'ui://source-intake/panel/v1' });
      assert.equal(resource.contents[0].mimeType, 'text/html;profile=mcp-app');
      assert.match(resource.contents[0].text, /源码导入/);
      assert.ok(!resource.contents[0].text.includes('src="/panel.js"'));
      assert.ok(!resource.contents[0].text.includes('src="/panel-bridge.js"'));
    });

    await t.test('real import preserves bytes, creates nested paths and binds the requirement snapshot', async () => {
      const source = join(temp, 'sample.js');
      const bytes = Buffer.from('\ufeff// 原始源码\r\nvar sample = 1;\r\n', 'utf8');
      await writeFile(source, bytes);
      const root = join(temp, '中文 新建项目');
      const p = await preview(root, [{ path: source }, { text: '<!-- doc.qa.sample.csp -->\n<html></html>\n' }]);
      assert.equal(await exists(join(root, 'frontend/scripts/dhcnewpro/dhcemc/sample.js')), false);
      assert.equal(p.plan.summary.ready, 2);
      const result = await call('panel_import', { preview_id: p.preview_id, conflict: 'skip' });
      assert.equal(result.ok, true);
      assert.equal(result.summary.written, 2);
      assert.deepEqual(await readFile(join(root, 'frontend/scripts/dhcnewpro/dhcemc/sample.js')), bytes);
      assert.ok(await exists(join(root, 'frontend/csp/doc.qa.sample.csp')));
      const requirement = JSON.parse(await readFile(join(result.batch_dir, 'requirement.json'), 'utf8'));
      assert.equal(requirement.id, 'BOS-PANEL-QA');
      const originals = await readdir(join(result.batch_dir, 'originals'));
      const snapshots = await Promise.all(originals.map(file => readFile(join(result.batch_dir, 'originals', file))));
      assert.ok(snapshots.some(buffer => buffer.equals(bytes)));
      assert.equal(await rejected('panel_import', { preview_id: p.preview_id }), true, 'preview must be single use');
    });

    await t.test('a stale source is refused rather than imported from a changed preview', async () => {
      const source = join(temp, 'changing.js');
      await writeFile(source, 'var version=1;', 'utf8');
      const root = join(temp, 'stale-target');
      const p = await preview(root, [{ path: source }], 'stale-source-session');
      await writeFile(source, 'var version=2;', 'utf8');
      assert.equal(await rejected('panel_import', { preview_id: p.preview_id }), true);
      assert.equal(await exists(join(root, 'frontend/scripts/dhcnewpro/dhcemc/changing.js')), false);
    });

    await t.test('the server rejects a forged browser request or expected plan', async () => {
      const root = join(temp, 'bound-root');
      const p = await preview(root, [{ text: 'var bound=1;', filename: 'bound.js' }], 'forgery-session');
      assert.equal(await rejected('panel_import', { preview_id: p.preview_id, request: { root: join(temp,'forged-root') }, plan: {} }), true);
      const result = await call('panel_import', { preview_id: p.preview_id });
      assert.equal(result.ok, true);
      assert.ok(await exists(join(root, 'frontend/scripts/dhcnewpro/dhcemc/bound.js')));
      assert.equal(await exists(join(temp, 'forged-root/frontend/scripts/dhcnewpro/dhcemc/bound.js')), false);
    });

    await t.test('two panels retain independent destination roots', async () => {
      const rootA = join(temp, 'panel-A'), rootB = join(temp, 'panel-B');
      const a = await preview(rootA, [{ text: 'var a=1;', filename: 'only-a.js' }], 'panel-A-session');
      const b = await preview(rootB, [{ text: 'var b=1;', filename: 'only-b.js' }], 'panel-B-session');
      assert.equal((await call('panel_import', { preview_id: a.preview_id })).ok, true);
      assert.equal((await call('panel_import', { preview_id: b.preview_id })).ok, true);
      assert.ok(await exists(join(rootA, 'frontend/scripts/dhcnewpro/dhcemc/only-a.js')));
      assert.ok(await exists(join(rootB, 'frontend/scripts/dhcnewpro/dhcemc/only-b.js')));
      assert.equal(await exists(join(rootA, 'frontend/scripts/dhcnewpro/dhcemc/only-b.js')), false);
      assert.equal(await exists(join(rootB, 'frontend/scripts/dhcnewpro/dhcemc/only-a.js')), false);
    });

    await t.test('a partially unresolved batch is rejected without writing the resolved source', async () => {
      const root = join(temp, 'blocked-batch');
      const p = await preview(root, [{ text: 'var known=1;', filename: 'known.js' }, { text: 'function unnamed() {}' }], 'blocked-batch-session');
      const result = await call('panel_import', { preview_id: p.preview_id });
      assert.equal(result.ok, false);
      assert.equal(result.status, 'blocked');
      assert.equal(await exists(join(root, 'frontend/scripts/dhcnewpro/dhcemc/known.js')), false);
    });
  } finally {
    await client.close().catch(() => {});
    await transport.close().catch(() => {});
    assert.equal(dirname(resolve(temp)), resolve(tmpdir()));
    await rm(temp, { recursive: true, force: true });
  }
});
