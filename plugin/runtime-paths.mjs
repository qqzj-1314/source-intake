import { access, readFile, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const PROJECT_ROOT = fileURLToPath(new URL('../', import.meta.url));
const ENTRIES = { server: 'bin/universal-server.mjs', doctor: 'bin/doctor.mjs' };
export const normalizedHash = bytes => createHash('sha256').update(Buffer.from(bytes).toString('utf8').replace(/\r\n/g, '\n')).digest('hex');
export const binaryHash = bytes => createHash('sha256').update(bytes).digest('hex');

export function checkNodeVersion(version = process.versions.node) {
  const [major, minor] = version.split('.').map(Number);
  if (!(major > 22 || major === 22 && minor >= 19)) throw new Error(`Node.js 22.19+ required (current ${version}). Skill-only Python CLI remains available.`);
}

async function inside(root, path) {
  if (typeof path !== 'string' || isAbsolute(path) || path.split(/[\\/]/).some(part => ['..', '.'].includes(part))) throw new Error('Invalid runtime manifest path');
  const actual = await realpath(resolve(root, path));
  const rel = relative(root, actual);
  if (!rel || rel === '..' || rel.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) || isAbsolute(rel)) throw new Error('Runtime path escapes project');
  return actual;
}

export async function verifyStandalone({ root = PROJECT_ROOT, kind = 'server' } = {}) {
  try {
    if (!ENTRIES[kind]) throw new Error(`Unknown runtime entry: ${kind}`);
    root = await realpath(root);
    const manifest = JSON.parse(await readFile(join(root, 'bin/BUILD.json'), 'utf8'));
    const pkg = JSON.parse(await readFile(join(root, 'plugin/package.json'), 'utf8'));
    if (manifest.schema_version !== 1 || manifest.version !== pkg.version) throw new Error('Standalone runtime version is stale');
    const entry = manifest.entries?.[kind];
    if (entry?.path !== ENTRIES[kind]) throw new Error('Standalone runtime manifest entry mismatch');
    const entryPath = await inside(root, entry.path);
    if (binaryHash(await readFile(entryPath)) !== entry.sha256) throw new Error('Standalone runtime integrity check failed');
    const sources = Object.entries(manifest.sources || {});
    if (!sources.length) throw new Error('Standalone runtime source manifest is empty');
    for (const [path, expected] of sources) {
      if (normalizedHash(await readFile(await inside(root, path))) !== expected) throw new Error(`Standalone runtime source changed: ${path}`);
    }
    return { ok: true, entry: entryPath, version: manifest.version, kind };
  } catch (error) { return { ok: false, reason: error.message }; }
}

export async function selectRuntime({ root = PROJECT_ROOT, kind = 'server' } = {}) {
  checkNodeVersion();
  if (!ENTRIES[kind]) throw new Error(`Unknown runtime entry: ${kind}`);
  const standalone = await verifyStandalone({ root, kind });
  if (standalone.ok) return { ...standalone, mode: 'standalone' };
  try {
    await access(join(root, 'plugin/node_modules/@modelcontextprotocol/sdk/package.json'));
    await access(join(root, 'plugin/node_modules/zod/package.json'));
    return { ok: true, mode: 'source', entry: join(root, kind === 'server' ? 'plugin/universal-server.mjs' : 'scripts/doctor.mjs'), fallback_reason: standalone.reason };
  } catch {
    throw new Error(`No verified standalone runtime (${standalone.reason}). Use an intact release package, or run npm --prefix plugin ci --ignore-scripts and node scripts/build-standalone.mjs. No dependencies were downloaded automatically.`);
  }
}
