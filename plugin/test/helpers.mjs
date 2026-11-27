import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer } from '../server.mjs';
import { DEFAULT_ENGINE, detectPython } from '../engine.mjs';

export const PYTHON = process.env.SOURCE_INTAKE_PYTHON || await detectPython();
export { DEFAULT_ENGINE };

export function createNative(overrides = {}) {
  const calls = [];
  return {
    calls,
    async clipboard() {
      calls.push('clipboard');
      return overrides.clipboard ? overrides.clipboard() : { ok: true, kind: 'empty' };
    },
    async chooseDirectory(initial) {
      calls.push(['chooseDirectory', initial]);
      return overrides.chooseDirectory ? overrides.chooseDirectory(initial) : { ok: true, canceled: true, path: null };
    },
    async chooseFiles(initial) {
      calls.push(['chooseFiles', initial]);
      return overrides.chooseFiles ? overrides.chooseFiles(initial) : { ok: true, canceled: true, paths: [] };
    },
  };
}

export async function fixture(t, options = {}) {
  const root = await mkdtemp(join(tmpdir(), 'source-intake-panel-test-'));
  const settingsDir = join(root, 'settings');
  const native = options.native || createNative();
  let server;
  let client;
  t.after(async () => {
    await options.beforeCleanup?.();
    await client?.close().catch(() => {});
    await server?.close().catch(() => {});
    await rm(root, { recursive: true, force: true });
  });
  server = await createServer({
    python: PYTHON,
    engine: DEFAULT_ENGINE,
    settingsDir,
    native,
    ...(options.server || {}),
  });
  client = new Client({ name: 'source-intake-panel-test', version: '0.1.0' });
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  const call = (name, args = {}) => client.callTool({ name, arguments: args });
  return { root, settingsDir, server, client, call, native };
}

export function previewArgs(root, sources, overrides = {}) {
  return {
    session_id: randomUUID(),
    root,
    project_name: '测试项目',
    profile: 'hisui',
    routes: {
      csp: 'frontend/csp',
      js: 'frontend/scripts/dhcnewpro/dhcemc',
      css: 'frontend/scripts/dhcnewpro/dhcemc/css',
      cls: 'backend',
    },
    cls_layout: 'package',
    requirement: { id: 'BOS-9001', title: '测试需求', description: '测试描述', url: 'http://example.invalid/9001' },
    sources,
    ...overrides,
  };
}

export async function writeSource(directory, name, data) {
  await mkdir(join(directory, '..'), { recursive: true });
  const path = join(directory, name);
  await mkdir(directory, { recursive: true });
  await writeFile(path, data);
  return path;
}

export async function writeLegacySettings(settingsDir, value) {
  await mkdir(settingsDir, { recursive: true });
  const path = join(settingsDir, 'settings.json');
  await writeFile(path, JSON.stringify(value, null, 2), 'utf8');
  return path;
}

export async function readJson(path) {
  return JSON.parse(await readFile(path, 'utf8'));
}
