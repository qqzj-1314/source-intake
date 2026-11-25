import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { fixture, previewArgs, writeSource } from './helpers.mjs';

const exists = async path => { try { await access(path); return true; } catch { return false; } };

test('preview tokens are random, single-use, expiring and rejected for forgery or extra plan fields', async t => {
  const { root, call } = await fixture(t);
  const source = await writeSource(join(root, '来源'), 'lifecycle.js', 'var lifecycle = 1;\n');
  const project = join(root, '生命周期项目');
  const preview = (await call('panel_preview', previewArgs(project, [{ path: source }]))).structuredContent;
  assert.match(preview.preview_id, /^[0-9a-f-]{36}$/);
  assert.equal(preview.plan.items[0].status, 'ready');
  assert.equal(await exists(project), false, 'preview never creates the project directory');

  const forged = await call('panel_import', { preview_id: randomUUID() });
  assert.equal(forged.isError, true);
  assert.match(forged.content[0].text, /重新预览/);

  const extra = await call('panel_import', {
    preview_id: preview.preview_id, conflict: 'skip', request: { root: join(root, '伪造') }, plan: {},
  });
  assert.equal(extra.isError, true, 'only preview_id and conflict are accepted');

  const imported = await call('panel_import', { preview_id: preview.preview_id, conflict: 'skip' });
  assert.equal(imported.isError, undefined, imported.content?.[0]?.text);
  assert.equal(imported.structuredContent.ok, true);
  assert.equal(imported.structuredContent.status, 'completed');
  assert.ok(await exists(join(project, 'frontend/scripts/dhcnewpro/dhcemc/lifecycle.js')));
  const repeated = await call('panel_import', { preview_id: preview.preview_id, conflict: 'skip' });
  assert.equal(repeated.isError, true, 'a preview token is consumed by the first import');

  const expiring = await fixture(t, { server: { previewTtlMs: 20 } });
  const expiringPreview = (await expiring.call('panel_preview', previewArgs(join(expiring.root, '过期项目'), [
    { text: 'var expiring = 1;\n', filename: 'expiring.js' },
  ]))).structuredContent;
  await new Promise(resolve => setTimeout(resolve, 80));
  const expired = await expiring.call('panel_import', { preview_id: expiringPreview.preview_id });
  assert.equal(expired.isError, true);
  assert.match(expired.content[0].text, /过期|失效|重新预览/);
  assert.equal(await exists(join(expiring.root, '过期项目')), false);
});

test('a newer preview in the same panel invalidates the old token, while separate panels and servers stay isolated', async t => {
  const { root, call } = await fixture(t);
  const rootA = join(root, '面板A');
  const rootB = join(root, '面板B');
  const session = 'same-panel-session';
  const first = (await call('panel_preview', previewArgs(rootA, [
    { text: 'var a = 1;\n', filename: 'only-a.js' },
  ], { session_id: session }))).structuredContent;
  const second = (await call('panel_preview', previewArgs(rootB, [
    { text: 'var b = 1;\n', filename: 'only-b.js' },
  ], { session_id: session }))).structuredContent;
  const stale = await call('panel_import', { preview_id: first.preview_id });
  assert.equal(stale.isError, true, 'editing and re-previewing in one panel invalidates the previous plan');
  const importedB = await call('panel_import', { preview_id: second.preview_id });
  assert.equal(importedB.structuredContent.ok, true);
  assert.ok(await exists(join(rootB, 'frontend/scripts/dhcnewpro/dhcemc/only-b.js')));
  assert.equal(await exists(rootA), false);

  const panelA = (await call('panel_preview', previewArgs(rootA, [
    { text: 'var a = 2;\n', filename: 'only-a.js' },
  ], { session_id: 'panel-a-second' }))).structuredContent;
  const other = await fixture(t);
  const crossServer = await other.call('panel_import', { preview_id: panelA.preview_id });
  assert.equal(crossServer.isError, true, 'preview tokens never mix across panel server instances');
  const importedA = await call('panel_import', { preview_id: panelA.preview_id });
  assert.equal(importedA.structuredContent.ok, true);
  assert.ok(await exists(join(rootA, 'frontend/scripts/dhcnewpro/dhcemc/only-a.js')));
  assert.equal(await exists(join(rootB, 'frontend/scripts/dhcnewpro/dhcemc/only-a.js')), false);
});

test('pending items block the entire batch and changed sources or targets are refused instead of overwritten', async t => {
  const { root, call } = await fixture(t);
  const blockedRoot = join(root, '阻塞项目');
  const blocked = (await call('panel_preview', previewArgs(blockedRoot, [
    { text: 'var known = 1;\n', filename: 'known.js' },
    { text: 'function unnamed() { return 1; }\n' },
  ]))).structuredContent;
  assert.equal(blocked.plan.summary.ready, 1);
  assert.equal(blocked.plan.summary.pending, 1);
  const blockedImport = await call('panel_import', { preview_id: blocked.preview_id });
  assert.equal(blockedImport.isError, undefined);
  assert.equal(blockedImport.structuredContent.ok, false);
  assert.equal(blockedImport.structuredContent.status, 'blocked');
  assert.equal(await exists(blockedRoot), false, 'a blocked batch creates neither root nor files');
  assert.equal((await call('panel_import', { preview_id: blocked.preview_id })).isError, true);

  const source = await writeSource(join(root, '来源'), 'changing.js', 'var version = 1;\n');
  const changedRoot = join(root, '变化项目');
  const changedPreview = (await call('panel_preview', previewArgs(changedRoot, [{ path: source }]))).structuredContent;
  await writeFile(source, 'var version = 2;\n', 'utf8');
  const changedImport = await call('panel_import', { preview_id: changedPreview.preview_id });
  assert.equal(changedImport.isError, true);
  assert.match(changedImport.content[0].text, /预览|重新/);
  assert.equal(await exists(join(changedRoot, 'frontend/scripts/dhcnewpro/dhcemc/changing.js')), false);

  const targetRoot = join(root, '目标变化项目');
  const targetPreview = (await call('panel_preview', previewArgs(targetRoot, [
    { text: 'var target = "new";\n', filename: 'target.js' },
  ]))).structuredContent;
  const targetPath = join(targetRoot, targetPreview.plan.items[0].destination);
  await mkdir(join(targetRoot, 'frontend/scripts/dhcnewpro/dhcemc'), { recursive: true });
  await writeFile(targetPath, 'var target = "created-after-preview";\n', 'utf8');
  const targetImport = await call('panel_import', { preview_id: targetPreview.preview_id });
  assert.equal(targetImport.isError, true);
  assert.equal(await readFile(targetPath, 'utf8'), 'var target = "created-after-preview";\n',
    'a target that appeared after preview is never overwritten');
});

test('default skip preserves an existing file and an explicit backup-replace batches the old bytes first', async t => {
  const { root, call } = await fixture(t);
  const source = await writeSource(join(root, '来源'), 'replace.js', 'var replace = "new";\n');
  const project = join(root, '替换项目');
  const target = join(project, 'frontend/scripts/dhcnewpro/dhcemc/replace.js');
  await mkdir(join(project, 'frontend/scripts/dhcnewpro/dhcemc'), { recursive: true });
  await writeFile(target, 'var replace = "old";\n', 'utf8');

  const first = (await call('panel_preview', previewArgs(project, [{ path: source }]))).structuredContent;
  assert.equal(first.plan.items[0].status, 'exists');
  const skipped = await call('panel_import', { preview_id: first.preview_id, conflict: 'skip' });
  assert.equal(skipped.structuredContent.ok, true);
  assert.equal(skipped.structuredContent.summary['skipped-existing'], 1);
  assert.equal(await readFile(target, 'utf8'), 'var replace = "old";\n');

  const second = (await call('panel_preview', previewArgs(project, [{ path: source }]))).structuredContent;
  assert.equal(second.plan.items[0].status, 'exists');
  const replaced = await call('panel_import', { preview_id: second.preview_id, conflict: 'backup-replace' });
  assert.equal(replaced.structuredContent.ok, true);
  assert.equal(await readFile(target, 'utf8'), 'var replace = "new";\n');
  const backups = await readdir(join(replaced.structuredContent.batch_dir, 'backups'));
  assert.equal(backups.length, 1);
  assert.equal(await readFile(join(replaced.structuredContent.batch_dir, 'backups', backups[0]), 'utf8'),
    'var replace = "old";\n');
});
