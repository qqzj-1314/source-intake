import test from 'node:test';
import assert from 'node:assert/strict';
import { cp, mkdtemp, access, readFile, writeFile, rm, realpath } from 'node:fs/promises';
import { dirname, join, relative } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { detectPython } from '../engine.mjs';
import { verifyStandalone, checkNodeVersion } from '../runtime-paths.mjs';

const run = promisify(execFile);
const project = fileURLToPath(new URL('../../', import.meta.url));

test('release runs without any development node_modules and refuses stale or damaged bundles', async t => {
  const temp = await mkdtemp(join(tmpdir(), 'source-intake-no-dependencies-'));
  let client;
  t.after(async () => {
    await client?.close().catch(() => {});
    const actual = await realpath(temp);
    assert.equal(dirname(actual), await realpath(tmpdir()));
    await rm(actual, { recursive: true, force: true });
  });
  const python = await detectPython();
  for (const folder of ['bin', 'plugin', 'scripts', 'skill']) {
    await cp(join(project, folder), join(temp, folder), { recursive: true, filter: path => {
      const parts = relative(project, path).split(/[\\/]/);
      return !parts.some(part => ['node_modules', '.local', '__pycache__', 'test'].includes(part));
    } });
  }
  await assert.rejects(access(join(temp, 'plugin/node_modules')), { code: 'ENOENT' });
  const verified = await verifyStandalone({ root: temp });
  assert.equal(verified.ok, true, verified.reason);
  const environment = { ...process.env, NODE_PATH: '', SOURCE_INTAKE_PYTHON: python, SOURCE_INTAKE_ENGINE: join(temp, 'skill/source-intake/scripts/source_intake.py') };
  await run(process.execPath, ['scripts/configure.mjs', '--client', 'generic', '--output', 'configs'], { cwd: temp, env: environment, timeout: 30000 });
  const config = JSON.parse(await readFile(join(temp, 'configs/generic.json'), 'utf8'));
  assert.equal(config.mcpServers.source_intake.args[0], join(temp, 'plugin/launch.mjs'));
  const health = await run(process.execPath, ['plugin/doctor-launch.mjs', '--config', 'configs/generic.json'], { cwd: temp, env: environment, timeout: 30000 });
  assert.equal(JSON.parse(health.stdout).ok, true);
  assert.equal(JSON.parse(health.stdout).status.runtime_mode, 'standalone');
  assert.equal(JSON.parse(health.stdout).status.engine, environment.SOURCE_INTAKE_ENGINE);
  client = new Client({ name: 'standalone-offline-qa', version: '1' });
  await client.connect(new StdioClientTransport({ command: process.execPath, args: [join(temp, 'plugin/launch.mjs')], env: environment, stderr: 'pipe' }));
  async function call(name, args) {
    const response = await client.callTool({ name, arguments: args });
    assert.ok(!response.isError, response.content?.[0]?.text);
    return response.structuredContent;
  }
  const root = join(temp, 'project');
  const preview = await call('intake_plan', { root, sources: [{ text: 'var portable = true;\r\n', filename: 'portable.js' }], requirement: { id: 'OFFLINE-QA', prompt: '保持接口。\r\n' } });
  const result = await call('intake_apply', { preview_id: preview.preview_id });
  assert.equal(result.ok, true);
  const found = await call('intake_list_requirements', { root, query: 'OFFLINE-QA' });
  assert.equal(found.matches, 1);
  const context = await call('intake_context', { directory: found.items[0].directory });
  assert.equal(context.prompt, '保持接口。\r\n');
  await client.close(); client = null;
  const source = join(temp, 'plugin/contracts.mjs');
  const original = await readFile(source);
  await writeFile(source, Buffer.concat([original, Buffer.from('\n// modified source\n')]));
  assert.equal((await verifyStandalone({ root: temp })).ok, false);
  await assert.rejects(run(process.execPath, ['plugin/doctor-launch.mjs', '--config', 'configs/generic.json'], { cwd: temp, env: environment }), error => /No verified standalone runtime/.test(error.stderr));
  await writeFile(source, original);
  const bundle = join(temp, 'bin/universal-server.mjs');
  await writeFile(bundle, Buffer.concat([await readFile(bundle), Buffer.from('\nthrow new Error("damaged bundle executed");\n')]));
  const damaged = await verifyStandalone({ root: temp });
  assert.equal(damaged.ok, false);
  assert.match(damaged.reason, /integrity/);
  await assert.rejects(run(process.execPath, ['plugin/launch.mjs'], { cwd: temp, env: environment }), error => /integrity/.test(error.stderr) && !/damaged bundle executed/.test(error.stderr));
  await assert.rejects(access(join(temp, 'plugin/node_modules')), { code: 'ENOENT' });
});

test('runtime reports unsupported Node versions before attempting to load SDK', () => {
  assert.throws(() => checkNodeVersion('20.19.0'), /22.19/);
  assert.throws(() => checkNodeVersion('22.18.0'), /22.19/);
  assert.doesNotThrow(() => checkNodeVersion('22.19.0'));
  assert.doesNotThrow(() => checkNodeVersion('24.0.0'));
});
