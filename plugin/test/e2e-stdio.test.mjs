import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { DEFAULT_ENGINE, PYTHON } from './helpers.mjs';

const pluginRoot = fileURLToPath(new URL('..', import.meta.url));

test('end-to-end stdio import keeps original snapshots, requirement binding and read-only existing project files', async t => {
  const temp = await mkdtemp(join(tmpdir(), 'source-intake-e2e-'));
  const localAppData = join(temp, 'localappdata');
  const project = join(temp, '业务项目');
  await mkdir(join(project, 'frontend', 'csp'), { recursive: true });
  const sentinel = join(project, 'README.md');
  await writeFile(sentinel, '原有业务文件，不得修改。\n', 'utf8');
  const existingTarget = join(project, 'frontend', 'csp', '需求-006.csp');
  const oldTargetBytes = Buffer.from('<!-- 需求-006.csp -->\r\n旧内容\r\n', 'utf8');
  await writeFile(existingTarget, oldTargetBytes);

  const sourceDir = join(temp, '来源');
  await mkdir(sourceDir, { recursive: true });
  const cspSource = join(sourceDir, '需求-006.csp');
  const cspBytes = Buffer.concat([
    Buffer.from([0xef, 0xbb, 0xbf]),
    Buffer.from('<!-- 需求-006.csp -->\r\n<csp:if condition="1">\r\n<div>需求</div>\r\n</csp:if>\r\n', 'utf8'),
  ]);
  await writeFile(cspSource, cspBytes);
  const jsText = '<script>alert("源码片段")</script>\nconst demo = 1;\n';

  const transport = new StdioClientTransport({
    command: process.execPath,
    args: [join(pluginRoot, 'server.mjs')],
    stderr: 'pipe',
    env: {
      ...process.env,
      LOCALAPPDATA: localAppData,
      SOURCE_INTAKE_PYTHON: PYTHON,
      SOURCE_INTAKE_ENGINE: DEFAULT_ENGINE,
    },
  });
  let diagnostics = '';
  transport.stderr?.on('data', chunk => { diagnostics = (diagnostics + chunk.toString()).slice(-4000); });
  const client = new Client({ name: 'source-intake-e2e', version: '1.0.0' });
  t.after(async () => {
    await client.close().catch(() => {});
    await transport.close().catch(() => {});
    await rm(temp, { recursive: true, force: true });
  });
  await client.connect(transport);

  const call = async (name, args = {}) => {
    const response = await client.callTool({ name, arguments: args });
    assert.ok(!response.isError, `${name}: ${response.content?.map(item => item.text).join(' ')} ${diagnostics}`);
    return response.structuredContent;
  };

  const snapshotBefore = {
    sentinel: await readFile(sentinel),
    target: await readFile(existingTarget),
  };
  const preview = await call('panel_preview', {
    session_id: 'e2e-stdio-session',
    root: project,
    project_name: '业务项目',
    profile: 'hisui',
    routes: {
      csp: 'frontend/csp',
      js: 'frontend/scripts/dhcnewpro/dhcemc',
      css: 'frontend/scripts/dhcnewpro/dhcemc/css',
      cls: 'backend',
    },
    cls_layout: 'package',
    requirement: { id: 'BOS-9001', title: '端到端需求', description: '验证原始快照与需求编号', url: 'http://example.invalid/e2e' },
    sources: [{ path: cspSource }, { text: jsText, filename: 'demo.js' }],
  });
  assert.equal(preview.plan.summary.total, 2);
  assert.equal(preview.plan.items[0].status, 'exists');
  assert.equal(preview.plan.items[1].status, 'ready');
  assert.equal(preview.plan.items[0].destination, 'frontend/csp/需求-006.csp');
  assert.equal(preview.plan.items[1].destination, 'frontend/scripts/dhcnewpro/dhcemc/demo.js');
  await assert.rejects(access(join(project, 'frontend/scripts/dhcnewpro/dhcemc/demo.js')), { code: 'ENOENT' },
    'preview does not write source targets');

  const imported = await call('panel_import', { preview_id: preview.preview_id, conflict: 'skip' });
  assert.equal(imported.ok, true);
  assert.equal(imported.status, 'completed');
  assert.equal(imported.summary.written, 1);
  assert.equal(imported.summary['skipped-existing'], 1);

  assert.deepEqual(await readFile(join(project, 'frontend/scripts/dhcnewpro/dhcemc/demo.js'), 'utf8'), jsText);
  const cspTargetBytes = await readFile(join(project, 'frontend/csp/需求-006.csp'));
  assert.deepEqual(cspTargetBytes, snapshotBefore.target, 'default skip preserves the existing project file');
  assert.deepEqual(await readFile(sentinel), snapshotBefore.sentinel, 'unrelated business files stay byte-identical');

  const batchDir = imported.batch_dir;
  assert.match(batchDir, /\.source-intake[\\/]batches[\\/]/);
  const requirement = JSON.parse(await readFile(join(batchDir, 'requirement.json'), 'utf8'));
  assert.equal(requirement.id, 'BOS-9001');
  assert.equal(requirement.system, 'manual');
  const manifest = JSON.parse(await readFile(join(batchDir, 'manifest.json'), 'utf8'));
  assert.equal(manifest.requirement.id, 'BOS-9001');
  const originals = await readdir(join(batchDir, 'originals'));
  assert.equal(originals.length, 2);
  const originalsBytes = await Promise.all(originals.map(name => readFile(join(batchDir, 'originals', name))));
  assert.ok(originalsBytes.some(bytes => bytes.equals(cspBytes)), 'the BOM/CRLF original snapshot is byte-identical');
  assert.ok(originalsBytes.some(bytes => bytes.equals(Buffer.from(jsText, 'utf8'))),
    'pasted HTML/script source is stored verbatim as text');
  assert.deepEqual(await readFile(existingTarget), snapshotBefore.target);

  const history = await call('panel_state');
  assert.equal(history.settings.paths.legacy_exists, false, 'the e2e run never wrote user preferences');
  await assert.rejects(access(join(localAppData, 'SourceIntake', 'panel-settings.json')), { code: 'ENOENT' });
});
