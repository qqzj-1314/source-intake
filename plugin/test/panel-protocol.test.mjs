import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { PANEL_URI } from '../server.mjs';
import { fixture, createNative, previewArgs, readJson, writeLegacySettings } from './helpers.mjs';

const APP_ONLY = ['panel_state', 'panel_choose_directory', 'panel_choose_files', 'panel_clipboard',
  'panel_preview', 'panel_import', 'panel_save_settings'];

test('real MCP initialize/tools/resources expose the panel resource and app-only tools', async t => {
  const { client, call } = await fixture(t);
  const initialized = client.getServerVersion();
  assert.equal(initialized.name, 'source_intake');

  const { tools } = await client.listTools();
  assert.deepEqual(tools.map(tool => tool.name).sort(), ['open_panel', ...APP_ONLY].sort());
  const opener = tools.find(tool => tool.name === 'open_panel');
  assert.equal(opener._meta.ui.resourceUri, PANEL_URI);
  assert.deepEqual(opener._meta['openai/ui'].entrypoints, [{ type: 'global' }, { type: 'thread' }]);
  assert.equal(opener.annotations.readOnlyHint, true);
  assert.equal(opener._meta.ui.visibility, undefined, 'open_panel stays visible to the model');
  for (const name of APP_ONLY) {
    const tool = tools.find(item => item.name === name);
    assert.deepEqual(tool._meta.ui, { resourceUri: PANEL_URI, visibility: ['app'] }, name);
  }

  const { resources } = await client.listResources();
  assert.equal(resources[0].uri, PANEL_URI);
  assert.equal(resources[0].mimeType, 'text/html;profile=mcp-app');
  assert.deepEqual(resources[0]._meta.ui, { prefersBorder: false, csp: {} });
  const { contents } = await client.readResource({ uri: PANEL_URI });
  assert.equal(contents[0].mimeType, 'text/html;profile=mcp-app');
  assert.equal(contents[0]._meta['openai/ui'].preferredDisplayMode, 'fullscreen');
  assert.deepEqual(contents[0]._meta['openai/ui'].availableDisplayModes, ['fullscreen']);
  const html = contents[0].text;
  assert.match(html, /ui\/initialize/);
  assert.match(html, /源码导入/);
  for (const id of ['root', 'choose-directory', 'recent-roots', 'requirement-id', 'requirement-title',
    'requirement-description', 'requirement-url', 'profile', 'route-csp', 'route-js', 'route-css', 'route-cls',
    'collect-clipboard', 'choose-files', 'paste-code', 'source-list', 'preview', 'import', 'conflict',
    'preview-summary', 'result', 'status']) {
    assert.match(html, new RegExp(`id="${id}"`), id);
  }
  assert.match(html, /默认跳过/);
  assert.match(html, /备份后替换/);
  assert.match(html, /hisui/);
  assert.match(html, /flat/);
  assert.doesNotMatch(html, /<script src="\/panel\.js"/);
  assert.doesNotMatch(html, /navigator\.clipboard/);
  assert.doesNotMatch(html, /localStorage/);
  assert.match(html, /textContent/);

  const opened = await call('open_panel');
  assert.equal(opened.isError, undefined);
  assert.match(opened.content[0].text, /源码导入面板已打开/);

  const script = await readFile(new URL('../panel.js', import.meta.url), 'utf8');
  assert.doesNotMatch(script, /innerHTML|insertAdjacentHTML|outerHTML|document\.write/,
    'source and settings are rendered through DOM text nodes only');
  assert.match(script, /textContent/);
  const bridge = await readFile(new URL('../panel-bridge.js', import.meta.url), 'utf8');
  assert.match(bridge, /2026-01-26/);
  assert.match(bridge, /ui\/initialize/);
});

test('panel_state merges legacy preferences; panel_save_settings never overwrites the old settings file', async t => {
  const { root, settingsDir, call } = await fixture(t);
  const legacyPath = await writeLegacySettings(settingsDir, {
    version: 1,
    recent_roots: ['D:\\旧项目'],
    projects: {
      'd:\\旧项目': { root: 'D:\\旧项目', project_name: '旧项目', profile: 'flat', routes: { csp: '', js: 'old/js' } },
    },
  });
  const legacyBefore = await readFile(legacyPath);

  const initial = (await call('panel_state')).structuredContent;
  assert.deepEqual(initial.settings.recent_roots, ['D:\\旧项目']);
  assert.equal(initial.settings.projects['d:\\旧项目'].project_name, '旧项目');
  assert.equal(initial.settings.projects['d:\\旧项目'].cls_layout, 'package');
  assert.equal(initial.settings.paths.legacy_exists, true);
  assert.equal(initial.defaults.profile, 'hisui');
  await assert.rejects(access(join(settingsDir, 'panel-settings.json')), { code: 'ENOENT' },
    'reading preferences must not create the panel settings file');

  const newRoot = join(root, '新项目');
  const saved = await call('panel_save_settings', {
    root: newRoot,
    project_name: '新项目',
    profile: 'flat',
    routes: { csp: '', js: 'new/js', css: '', cls: '' },
    cls_layout: 'fullname',
  });
  assert.equal(saved.isError, undefined);
  assert.equal(saved.structuredContent.settings.recent_roots[0], newRoot);
  const merged = (await call('panel_state')).structuredContent;
  assert.ok(merged.settings.recent_roots.includes('D:\\旧项目'), 'legacy recent roots stay visible');
  const project = Object.values(merged.settings.projects).find(value => value.project_name === '新项目');
  assert.equal(project.profile, 'flat');
  assert.equal(project.cls_layout, 'fullname');
  assert.equal(project.routes.js, 'new/js');
  const panelSettings = await readJson(join(settingsDir, 'panel-settings.json'));
  assert.equal(panelSettings.version, 1);
  assert.ok(!JSON.stringify(panelSettings).includes('BOS-'), 'no requirement or source content is stored in preferences');
  assert.deepEqual(await readFile(legacyPath), legacyBefore, 'the old desktop settings file is only read');
});

test('native clipboard and file pickers run only on user-triggered tools and respect cancellation', async t => {
  const { root, call, native } = await fixture(t, {
    native: createNative({
      clipboard: () => ({ ok: true, kind: 'files', files: ['C:\\来源\\a.csp', 'C:\\来源\\b.js'] }),
      chooseDirectory: () => ({ ok: true, canceled: false, path: join(root, '已选目录') }),
      chooseFiles: () => ({ ok: true, canceled: false, paths: ['C:\\来源\\a.csp'] }),
    }),
  });
  const targetPath = join(root, '已选目录');
  assert.equal(native.calls.length, 0, 'connecting and reading state never touches the native clipboard');
  await call('panel_state');
  assert.equal(native.calls.length, 0);
  await assert.rejects(access(targetPath), { code: 'ENOENT' });

  const clipboard = (await call('panel_clipboard')).structuredContent;
  assert.equal(clipboard.kind, 'files');
  assert.deepEqual(clipboard.files, ['C:\\来源\\a.csp', 'C:\\来源\\b.js']);
  const directory = (await call('panel_choose_directory', { initial: root })).structuredContent;
  assert.equal(directory.canceled, false);
  assert.equal(directory.path, targetPath);
  await assert.rejects(access(targetPath), { code: 'ENOENT' }, 'choosing a directory never creates it');
  const files = (await call('panel_choose_files', { initial: root })).structuredContent;
  assert.deepEqual(files.paths, ['C:\\来源\\a.csp']);
  assert.equal(native.calls.length, 3);

  const cancelledFixture = await fixture(t, {
    native: createNative({
      chooseDirectory: () => ({ ok: true, canceled: true, path: null }),
      chooseFiles: () => ({ ok: true, canceled: true, paths: [] }),
    }),
  });
  const cancelledDirectory = (await cancelledFixture.call('panel_choose_directory')).structuredContent;
  assert.deepEqual(cancelledDirectory, { canceled: true, path: null });
  const cancelledFiles = (await cancelledFixture.call('panel_choose_files')).structuredContent;
  assert.deepEqual(cancelledFiles, { canceled: true, paths: [] });
  assert.equal(cancelledFixture.native.calls.length, 2);
});

test('pasted source containing HTML script fragments is planned as text and never echoed as executable markup', async t => {
  const { root, call } = await fixture(t);
  const text = '<script>alert("注入")</script>\nconst value = 1;\n';
  const response = await call('panel_preview', previewArgs(root, [
    { text, filename: 'demo.js' },
  ]));
  assert.equal(response.isError, undefined, response.content?.[0]?.text);
  const fields = response.structuredContent;
  assert.match(fields.preview_id, /^[0-9a-f-]{36}$/);
  const item = fields.plan.items[0];
  assert.equal(item.status, 'ready');
  assert.equal(item.kind, 'js');
  assert.equal(item.filename, 'demo.js');
  assert.equal(item.destination, 'frontend/scripts/dhcnewpro/dhcemc/demo.js');
  assert.ok(!JSON.stringify(fields).includes('alert("注入")'), 'the preview response does not carry pasted source markup as HTML');
  const source = await readFile(new URL('../panel.js', import.meta.url), 'utf8');
  assert.match(source, /textContent/);
  assert.doesNotMatch(source, /innerHTML/);

  const invalid = await call('panel_preview', previewArgs(root, [{ text: '', filename: 'empty.js' }]));
  assert.equal(invalid.isError, true);
  const missing = await call('panel_preview', previewArgs(root, [{ path: join(root, '不存在.csp') }]));
  assert.equal(missing.structuredContent?.plan?.items?.[0]?.status, 'invalid');
});
