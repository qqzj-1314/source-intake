import { execFile } from 'node:child_process';
import { mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const run = promisify(execFile);

export const DEFAULT_ENGINE = fileURLToPath(new URL('../skill/source-intake/scripts/source_intake.py', import.meta.url));
const TMP_PREFIX = 'source-intake-panel-';

export class EngineError extends Error {
  constructor(message, { type = 'EngineError', exitCode = null, payload = null } = {}) {
    super(message);
    this.name = 'EngineError';
    this.type = type;
    this.exitCode = exitCode;
    this.payload = payload;
  }
}

export async function detectPython(candidates = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python']) {
  if (process.env.SOURCE_INTAKE_PYTHON) return process.env.SOURCE_INTAKE_PYTHON;
  const failures = [];
  for (const candidate of candidates) {
    try {
      const args = candidate === 'py' && process.platform === 'win32'
        ? ['-3', '-c', 'import sys; print(sys.executable)']
        : ['-c', 'import sys; print(sys.executable)'];
      const { stdout } = await run(candidate, args, { windowsHide: true, timeout: 15000, encoding: 'utf8' });
      const path = stdout.trim().split(/\r?\n/)[0];
      if (path) return path;
    } catch (error) {
      failures.push(`${candidate}: ${error.code || error.message}`);
    }
  }
  throw new EngineError(`未检测到可用的 Python：${failures.join('；')}`);
}

function tail(text, limit = 1200) {
  const value = typeof text === 'string' ? text.trim() : '';
  return value.length > limit ? value.slice(-limit) : value;
}

async function runCli({ python, engine, args, timeoutMs }) {
  const options = {
    windowsHide: true,
    shell: false,
    timeout: timeoutMs,
    maxBuffer: 32 * 1024 * 1024,
    encoding: 'utf8',
    cwd: tmpdir(),
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
  };
  let stdout;
  let stderr;
  let exitCode = 0;
  try {
    ({ stdout, stderr } = await run(python, [engine, ...args], options));
  } catch (error) {
    if (error.killed || error.signal) {
      throw new EngineError('源码导入内核执行超时，导入状态可能不完整，请检查批次记录后重新预览。', { type: 'EngineTimeoutError' });
    }
    stdout = error.stdout;
    stderr = error.stderr;
    exitCode = typeof error.code === 'number' ? error.code : null;
  }
  const text = String(stdout || '').trim();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      if (exitCode !== 0) {
        throw new EngineError(tail(stderr) || `源码导入内核退出码 ${exitCode}。`, {
          type: 'EngineProcessError', exitCode,
        });
      }
      throw new EngineError(`源码导入内核输出无法解析为 JSON：${tail(text, 300)}`, { type: 'EngineOutputError' });
    }
  }
  if (!payload || typeof payload !== 'object') {
    throw new EngineError(tail(stderr) || '源码导入内核没有返回 JSON 输出。', { type: 'EngineOutputError', exitCode });
  }
  return { payload, exitCode, stderr };
}

async function cleanupTemp(directory) {
  let realDir;
  let realRoot;
  try {
    realRoot = await realpath(tmpdir());
    realDir = await realpath(directory);
  } catch {
    return;
  }
  if (dirname(realDir) !== realRoot || !basename(realDir).startsWith(TMP_PREFIX)) {
    throw new EngineError('临时目录校验失败，已拒绝清理。');
  }
  await rm(realDir, { recursive: true, force: true });
}

export async function planRequest({ python, engine = DEFAULT_ENGINE, request }) {
  const temp = await mkdtemp(join(tmpdir(), TMP_PREFIX));
  try {
    const manifest = join(temp, 'request.json');
    await writeFile(manifest, JSON.stringify(request), 'utf8');
    const { payload, exitCode, stderr } = await runCli({
      python, engine, args: ['plan', '--manifest', manifest], timeoutMs: 60000,
    });
    if (exitCode !== 0 || payload.ok !== true) {
      const message = payload.error?.message || tail(stderr) || '源码导入内核未能生成计划。';
      throw new EngineError(message, {
        type: payload.error?.type || 'PlanError', exitCode, payload,
      });
    }
    return payload;
  } finally {
    await cleanupTemp(temp);
  }
}

export async function applyPlan({ python, engine = DEFAULT_ENGINE, request, plan, conflict = 'skip' }) {
  const temp = await mkdtemp(join(tmpdir(), TMP_PREFIX));
  try {
    const manifest = join(temp, 'request.json');
    const expected = join(temp, 'plan.json');
    await writeFile(manifest, JSON.stringify(request), 'utf8');
    await writeFile(expected, JSON.stringify(plan), 'utf8');
    return await runCli({
      python,
      engine,
      args: ['apply', '--manifest', manifest, '--conflict', conflict, '--expected-plan', expected],
      timeoutMs: 120000,
    });
  } finally {
    await cleanupTemp(temp);
  }
}

export async function readHistory({ python, engine = DEFAULT_ENGINE, root }) {
  const response = await runCli({ python, engine, args: ['history', '--root', root], timeoutMs: 30000 });
  if (response.exitCode !== 0) throw new EngineError(response.payload.error?.message || '无法读取导入历史。');
  return response.payload;
}

export async function readRequirementContext({ python, directory }) {
  const script = fileURLToPath(new URL('../skill/source-intake/scripts/requirement_context.py', import.meta.url));
  const response = await runCli({ python, engine: script, args: ['--directory', directory], timeoutMs: 30000 });
  if (response.exitCode !== 0) throw new EngineError(response.payload.error?.message || '无法读取需求材料。');
  return response.payload;
}

export async function listRequirementPackages({ python, root, query = '', limit = 50 }) {
  const script = fileURLToPath(new URL('../skill/source-intake/scripts/requirement_index.py', import.meta.url));
  const response = await runCli({ python, engine: script, args: ['--root', root, '--query', query, '--limit', String(limit)], timeoutMs: 30000 });
  if (response.exitCode !== 0) throw new EngineError(response.payload.error?.message || '无法列出需求目录。');
  return response.payload;
}

export { cleanupTemp };
