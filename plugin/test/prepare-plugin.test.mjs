import test from 'node:test';
import assert from 'node:assert/strict';
import { access, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { preparePlugin } from '../prepare-plugin.mjs';
import { DEFAULT_ENGINE, PYTHON } from './helpers.mjs';

test('prepare-plugin builds an independent local marketplace without hooks or board files', async t => {
  const temp = await mkdtemp(join(tmpdir(), 'source-intake-prepare-'));
  t.after(() => rm(temp, { recursive: true, force: true }));
  const root = join(temp, 'marketplace');
  const prepared = await preparePlugin({ root, python: PYTHON });
  assert.equal(prepared.selector, 'source-intake@source-intake-local');

  const catalog = JSON.parse(await readFile(join(root, '.agents', 'plugins', 'marketplace.json'), 'utf8'));
  assert.equal(catalog.name, 'source-intake-local');
  assert.equal(catalog.plugins.length, 1);
  assert.equal(catalog.plugins[0].name, 'source-intake');
  assert.deepEqual(catalog.plugins[0].source, { source: 'local', path: './plugins/source-intake' });

  const pluginJson = JSON.parse(await readFile(join(prepared.plugin, '.codex-plugin', 'plugin.json'), 'utf8'));
  assert.equal(pluginJson.name, 'source-intake');
  assert.equal(pluginJson.interface.displayName, '源码导入');
  assert.equal(pluginJson.mcpServers, './.mcp.json');
  assert.equal(pluginJson.hooks, undefined, 'the plugin exposes no hooks');
  await assert.rejects(access(join(prepared.plugin, 'hooks')), { code: 'ENOENT' });
  await assert.rejects(access(join(root, 'plugins', 'agents-board')), { code: 'ENOENT' }, 'the board marketplace is untouched');

  const mcp = JSON.parse(await readFile(join(prepared.plugin, '.mcp.json'), 'utf8'));
  const sourceIntake = mcp.mcpServers.source_intake;
  assert.ok(sourceIntake);
  assert.equal(sourceIntake.command, process.execPath);
  assert.equal(sourceIntake.args.length, 1);
  assert.equal(sourceIntake.args[0], prepared.server);
  assert.ok(sourceIntake.args[0].endsWith('server.mjs'));
  assert.equal(sourceIntake.env.SOURCE_INTAKE_PYTHON, PYTHON);
  assert.equal(sourceIntake.env.SOURCE_INTAKE_ENGINE, DEFAULT_ENGINE);
  assert.ok(sourceIntake.env.SOURCE_INTAKE_ENGINE.endsWith('source_intake.py'));
  assert.equal(sourceIntake.startup_timeout_sec, 20);
  await access(sourceIntake.env.SOURCE_INTAKE_PYTHON);
  await access(sourceIntake.env.SOURCE_INTAKE_ENGINE);
  await access(prepared.server);

  const again = await preparePlugin({ root, python: PYTHON });
  assert.equal(again.selector, prepared.selector);
});
