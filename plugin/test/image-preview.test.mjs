import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile, access, readdir, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fixture, createNative, previewArgs } from './helpers.mjs';

const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6s8AAAAASUVORK5CYII=', 'base64');

test('pasted image bytes preview and import separately without reading native clipboard', async t => {
  const { root, call, native, server } = await fixture(t);
  const paths = [];
  for (let i = 0; i < 2; i++) {
    const response = await call('panel_add_image', { image_base64: png.toString('base64'), mime_type: 'image/png' });
    assert.ok(!response.isError, response.content?.[0]?.text);
    paths.push(response.structuredContent.path);
  }
  assert.notEqual(paths[0], paths[1]);
  assert.deepEqual(native.calls, []);
  for (const path of paths) {
    const response = await call('panel_image_preview', { path });
    assert.ok(!response.isError, response.content?.[0]?.text);
    assert.deepEqual(Buffer.from(response.structuredContent.data_url.split(',')[1], 'base64'), png);
  }
  const plan = await call('panel_preview', previewArgs(join(root, 'project'), paths.map(path => ({ path, role: 'attachment' })), { work_package: true }));
  const token = plan.structuredContent.preview_id;
  // Opening a content preview must not consume or invalidate the import plan.
  assert.ok(!(await call('panel_image_preview', { path: paths[0] })).isError);
  const imported = await call('panel_import', { preview_id: token });
  assert.equal(imported.structuredContent.ok, true);
  const attachments = imported.structuredContent.work_package.attachments_dir;
  const names = await readdir(attachments);
  assert.equal(names.length, 2);
  for (const name of names) assert.deepEqual(await readFile(join(attachments, name)), png);
  await server.close();
  for (const path of paths) await assert.rejects(access(path), { code: 'ENOENT' });
  assert.equal((await readdir(attachments)).length, 2, 'temporary cleanup does not remove imported attachments');
});

test('only collected raster files can be read for preview; SVG and HTML are rejected', async t => {
  let selected = [];
  const native = createNative({ chooseFiles: () => ({ canceled: false, paths: selected }) });
  const { root, call } = await fixture(t, { native });
  const path = join(root, 'selected.png');
  await writeFile(path, png);
  assert.equal((await call('panel_image_preview', { path })).isError, true);
  selected = [path];
  await call('panel_choose_files');
  assert.equal((await call('panel_image_preview', { path })).structuredContent.mime_type, 'image/png');
  await writeFile(path, '<svg><script>bad()</script></svg>');
  assert.equal((await call('panel_image_preview', { path })).isError, true);
  assert.equal((await call('panel_add_image', { image_base64: png.toString('base64'), mime_type: 'image/jpeg' })).isError, true);
  assert.equal((await call('panel_add_image', { image_base64: Buffer.from('<html>bad</html>').toString('base64'), mime_type: 'image/png' })).isError, true);
  const folder = join(root, 'folder.png');
  await mkdir(folder); selected = [folder]; await call('panel_choose_files');
  assert.equal((await call('panel_image_preview', { path: folder })).isError, true);
});
