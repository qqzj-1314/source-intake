import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const NATIVE_SCRIPT = fileURLToPath(new URL('./native_io.py', import.meta.url));

export class NativeError extends Error {
  constructor(message) {
    super(message);
    this.name = 'NativeError';
  }
}

function invoke(python, command, payload, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, [NATIVE_SCRIPT, command], {
      shell: false,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
    });
    let stdout = '';
    let stderr = '';
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve(value);
    };
    const timer = setTimeout(() => {
      child.kill();
      finish(new NativeError('原生操作超时，请重新选择。'));
    }, timeoutMs);
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', chunk => { if (stdout.length < 4 * 1024 * 1024) stdout += chunk; });
    child.stderr.on('data', chunk => { if (stderr.length < 64 * 1024) stderr += chunk; });
    child.on('error', error => finish(new NativeError(`无法启动原生桥：${error.message}`)));
    child.on('close', code => {
      if (settled) return;
      let value;
      try {
        value = JSON.parse(stdout.trim());
      } catch {
        finish(new NativeError(stderr.trim() || `原生桥输出无效（退出码 ${code}）。`));
        return;
      }
      if (!value || typeof value !== 'object') {
        finish(new NativeError('原生桥返回了无效结果。'));
      } else if (value.ok !== true) {
        finish(new NativeError(value.error?.message || '原生操作失败。'));
      } else {
        finish(null, value);
      }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(payload ?? {}), 'utf8');
  });
}

export function createNativeBridge({ python, timeoutMs = 120000 } = {}) {
  if (!python) throw new NativeError('缺少已检测的 Python 路径。');
  return {
    clipboard: () => invoke(python, 'clipboard', {}, timeoutMs),
    chooseDirectory: initial => invoke(python, 'choose_directory', { initial }, timeoutMs),
    chooseFiles: initial => invoke(python, 'choose_files', { initial }, timeoutMs),
  };
}
