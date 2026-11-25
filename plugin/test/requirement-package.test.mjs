import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture, createNative } from './helpers.mjs';

test('MCP imports screenshot and document with a verbatim supplemental prompt and rejects replay', async t => {
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6s8AAAAASUVORK5CYII=', 'base64');
  let screenshots = 0;
  const native = { ...createNative(), screenshot: async () => { screenshots++; return { png_base64: png.toString('base64') }; } };
  const { root, call } = await fixture(t, { native });
  assert.equal(screenshots, 0);
  const captured = (await call('panel_screenshot')).structuredContent;
  assert.equal(screenshots, 1);
  const document = join(root, '需求.pdf');
  await writeFile(document, Buffer.from('%PDF-1.7\nsynthetic requirement'));
  const prompt = '  按截图修改。\r\n保留接口签名。\n';
  const preview = await call('panel_preview', { root: join(root, 'project'), sources: [{ path: captured.path, role: 'attachment' }, { path: document, role: 'attachment' }], work_package: true, requirement: { id: 'QA-ONLY-ATTACHMENTS', description: '文档中的需求', prompt } });
  assert.ok(!preview.isError, preview.content?.[0]?.text);
  const token = preview.structuredContent.preview_id;
  const imported = (await call('panel_import', { preview_id: token })).structuredContent;
  assert.equal(imported.ok, true);
  assert.equal(await readFile(imported.work_package.prompt, 'utf8'), prompt);
  const files = await readdir(imported.work_package.attachments_dir);
  assert.equal(files.length, 2);
  assert.deepEqual(await readFile(join(imported.work_package.attachments_dir, files.find(f => f.endsWith('.png')))), png);
  const replay = await call('panel_import', { preview_id: token });
  assert.equal(replay.isError, true);
});

test('MCP supports blank requirement directory with no source files', async t => {
  const { root, call } = await fixture(t);
  const preview = (await call('panel_preview', { root: join(root, 'blank'), sources: [], work_package: true, requirement: { prompt: '后续自行放材料' } })).structuredContent;
  const result = (await call('panel_import', { preview_id: preview.preview_id })).structuredContent;
  assert.equal(result.ok, true);
  assert.deepEqual(await readdir(result.work_package.attachments_dir), []);
  assert.match(await readFile(result.work_package.readme, 'utf8'), /重新枚举 attachments/);
});
