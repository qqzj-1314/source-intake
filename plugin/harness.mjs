import { createServer as createHttpServer } from 'node:http';
import { mkdir, mkdtemp, realpath, rm, writeFile, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { randomUUID } from 'node:crypto';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer, panelHtml } from './server.mjs';
import { DEFAULT_ENGINE, detectPython } from './engine.mjs';

const HARNESS_PREFIX = 'source-intake-harness-';

function within(child, parent) {
  const rel = relative(resolve(parent), resolve(child));
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel));
}

function hostPage(token) {
  const tokenJson = JSON.stringify(token).replace(/</g, '\\u003c');
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>源码导入 · 本地验收 Harness</title>
<style>
  :root{color-scheme:light dark;--ground:#f4f4f4;--surface:#fff;--border:#e4e4e4;--text:#141414;--muted:#5f5f5f;--accent:#3B6E5A}
  *{box-sizing:border-box}html,body{height:100%}
  body{margin:0;display:flex;flex-direction:column;background:var(--ground);color:var(--text);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--border)}
  header strong{font-size:14px}header span{color:var(--muted);font-size:12px}
  iframe{flex:1 1 auto;width:100%;border:0;background:var(--ground)}
  @media(prefers-color-scheme:dark){:root{--ground:#101010;--surface:#171717;--border:#2c2c2c;--text:#f2f2f2;--muted:#b0b0b0;--accent:#7fbf96}}
</style>
<script>
(() => {
  const TOKEN = ${tokenJson};
  window.addEventListener('message', async event => {
    const frame = document.getElementById('panel');
    if (!frame || event.source !== frame.contentWindow || event.data?.jsonrpc !== '2.0') return;
    const send = message => frame.contentWindow.postMessage(message, '*');
    const message = event.data;
    if (message.method === 'ui/initialize') {
      send({ jsonrpc: '2.0', id: message.id, result: {
        protocolVersion: '2026-01-26',
        hostContext: { theme: window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light' },
      } });
      return;
    }
    if (message.method === 'ui/notifications/initialized') return;
    if (message.method === 'tools/call') {
      try {
        const response = await fetch('/rpc', {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-source-intake-harness': TOKEN },
          body: JSON.stringify({ name: message.params?.name, arguments: message.params?.arguments || {} }),
        });
        const body = await response.json();
        if (!response.ok || body.ok !== true) {
          send({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: body?.error || '请求失败' } });
        } else {
          send({ jsonrpc: '2.0', id: message.id, result: body.result });
        }
      } catch (error) {
        send({ jsonrpc: '2.0', id: message.id, error: { code: -32000, message: error.message } });
      }
      return;
    }
    if (message.id !== undefined) send({ jsonrpc: '2.0', id: message.id, error: { code: -32601, message: 'Method not supported' } });
  });
})();
</script>
</head>
<body>
  <header><strong>源码导入 · 本地验收 Harness</strong><span>仅 127.0.0.1 临时端口；面板、工具和导入都使用临时演示数据，不是生产入口。</span></header>
  <iframe id="panel" src="/panel" title="源码导入面板"></iframe>
</body></html>`;
}

async function setupDemo(root) {
  const sourceDir = join(root, '演示来源');
  await mkdir(sourceDir, { recursive: true });
  const files = {
    image: join(sourceDir, '需求截图.png'),
    document: join(sourceDir, '需求文档.pdf'),
    js: join(sourceDir, 'demo.js'),
    css: join(sourceDir, 'demo.css'),
    csp: join(sourceDir, 'demo.csp'),
  };
  await writeFile(files.image, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6s8AAAAASUVORK5CYII=', 'base64'));
  await writeFile(files.document, '%PDF-1.7\nSynthetic requirement document\n');
  await writeFile(files.js, '// demo.js\nconst banner = "<script>alert(\\"演示\\")</script>";\nfunction demo() { return banner; }\n', 'utf8');
  await writeFile(files.css, '/* demo.css */\n.demo-card { color: #3B6E5A; padding: 8px; }\n', 'utf8');
  await writeFile(files.csp, '<!-- demo.csp -->\n<csp:if condition="1">\n<div class="demo-card">演示</div>\n</csp:if>\n', 'utf8');
  return {
    sourceDir,
    files,
    projectRoot: join(root, '演示项目'),
  };
}

function createSyntheticBosClient() {
  let connected = false;
  const record = {
    '需求号': 1001,
    '标题': '合成 BOS 需求 <script>alert("仅文本")</script>',
    '描述': '仅用于本地验收的合成描述，不包含真实业务内容。',
    '操作时间': '2026-01-02 03:04:05',
    '创建时间': '2026-01-01 01:02:03',
    '状态': '合成待处理',
    '类型': '合成类型',
    '来源': '合成来源',
    '发起组': '合成发起组',
    '发起人': '合成发起人',
    '接受组': '合成接受组',
    '接受人': '合成接受人',
    '当前组': '合成当前组',
    '当前人': '合成当前人',
    '产品': '合成产品',
    '产品名': '合成产品名',
    '模块': '合成模块',
    '附件': [],
  };
  return {
    status() { return { configured: true, connected }; },
    async query(args) {
      if (!args.id && !args.tuser && !args.tgroup) throw new Error('BOS 查询必须提供 id，或至少一个按组或按人条件。');
      let items = [record];
      if (args.id && args.id !== String(record['需求号'])) items = [];
      if (args.tuser && args.tuser !== record['接受人']) items = [];
      if (args.tgroup && args.tgroup !== record['接受组']) items = [];
      if (args.title && !record['标题'].includes(args.title)) items = [];
      connected = true;
      return { items, count: items.length, total: items.length, truncated: false };
    },
    async close() {},
  };
}

export async function startHarness({ python, engine = DEFAULT_ENGINE, host = '127.0.0.1', port = 0, bos } = {}) {
  const pythonPath = python || await detectPython();
  const root = await mkdtemp(join(tmpdir(), HARNESS_PREFIX));
  const settingsDir = join(root, 'settings');
  const demo = await setupDemo(root);
  const token = randomUUID();
  const native = {
    clipboard: async () => ({ ok: true, kind: 'files', files: [demo.files.js, demo.files.css, demo.files.csp] }),
    chooseDirectory: async () => ({ ok: true, canceled: false, path: demo.projectRoot }),
    chooseFiles: async (initial, purpose) => ({ ok: true, canceled: false, paths: purpose === 'attachment' ? [demo.files.image, demo.files.document] : [demo.files.js, demo.files.css, demo.files.csp] }),
    screenshot: async () => ({ ok: true, png_base64: (await readFile(demo.files.image)).toString('base64') }),
  };
  const mcp = await createServer({ python: pythonPath, engine, settingsDir, native, serverName: 'source_intake_harness', bos: bos || createSyntheticBosClient() });
  const client = new Client({ name: 'source-intake-harness', version: '0.1.0' });
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  await mcp.connect(serverTransport);
  await client.connect(clientTransport);

  const allowedTools = new Set(['open_panel', 'panel_state', 'panel_choose_directory', 'panel_choose_files',
    'panel_clipboard', 'panel_preview', 'panel_import', 'panel_save_settings', 'panel_screenshot', 'panel_list_requirements', 'panel_add_image', 'panel_image_preview',
    'panel_bos_status', 'panel_bos_query']);
  const screenshotPaths = new Set();

  async function restrictedCall(name, args) {
    if (!allowedTools.has(name)) throw new Error('该工具在验收 Harness 中不可用。');
    if (['panel_screenshot', 'panel_add_image'].includes(name)) {
      const response = await client.callTool({ name, arguments: args });
      if (response.structuredContent?.path) screenshotPaths.add(await realpath(response.structuredContent.path));
      return response;
    }
    if (name === 'panel_preview') {
      if (!within(args.root, root)) throw new Error('Harness 只允许操作临时演示目录。');
      let realRoot = resolve(root);
      try { realRoot = await realpath(root); } catch {}
      for (const source of args.sources || []) {
        if (typeof source.path !== 'string') continue;
        let candidate = resolve(source.path);
        try { candidate = await realpath(candidate); } catch {}
        if (!within(candidate, realRoot) && !screenshotPaths.has(candidate)) throw new Error('Harness 只允许导入临时演示来源。');
      }
    }
    if (['panel_save_settings', 'panel_list_requirements'].includes(name) && !within(args.root, root)) {
      throw new Error('Harness 只允许保存临时演示目录。');
    }
    return client.callTool({ name, arguments: args });
  }

  const panel = await panelHtml();
  const page = hostPage(token);
  const http = createHttpServer(async (request, response) => {
    try {
      const url = new URL(request.url || '/', `http://${host}:${port}`);
      if (request.method === 'GET' && url.pathname === '/') {
        response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
        response.end(page);
        return;
      }
      if (request.method === 'GET' && url.pathname === '/panel') {
        response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
        response.end(panel);
        return;
      }
      if (request.method === 'POST' && url.pathname === '/rpc') {
        if (request.headers['x-source-intake-harness'] !== token) {
          response.writeHead(403, { 'content-type': 'application/json; charset=utf-8' });
          response.end(JSON.stringify({ ok: false, error: '验收令牌无效。' }));
          return;
        }
        let raw = '';
        for await (const chunk of request) {
          raw += chunk;
          if (raw.length > 29 * 1024 * 1024) throw new Error('请求过大。');
        }
        const body = JSON.parse(raw || '{}');
        if (typeof body.name !== 'string' || !body.arguments || typeof body.arguments !== 'object') {
          throw new Error('请求格式无效。');
        }
        try {
          const result = await restrictedCall(body.name, body.arguments);
          response.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
          response.end(JSON.stringify({ ok: true, result }));
        } catch (error) {
          response.writeHead(400, { 'content-type': 'application/json; charset=utf-8' });
          response.end(JSON.stringify({ ok: false, error: error.message }));
        }
        return;
      }
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Not Found');
    } catch (error) {
      if (!response.headersSent) response.writeHead(500, { 'content-type': 'application/json; charset=utf-8' });
      response.end(JSON.stringify({ ok: false, error: error.message }));
    }
  });
  await new Promise((resolvePromise, reject) => {
    http.once('error', reject);
    http.listen(port, host, resolvePromise);
  });
  const address = http.address();
  const url = `http://${host}:${address.port}/`;
  const closed = { value: false };
  async function close() {
    if (closed.value) return;
    closed.value = true;
    await new Promise(resolvePromise => http.close(resolvePromise));
    await client.close().catch(() => {});
    await mcp.close().catch(() => {});
    const realRoot = await realpath(root).catch(() => root);
    const realTemp = await realpath(tmpdir());
    if (resolve(realRoot, '..') === realTemp && realRoot.split(/[\\/]/).pop().startsWith(HARNESS_PREFIX)) {
      await rm(realRoot, { recursive: true, force: true });
    }
  }
  return { url, root, settingsDir, demo, token, close, server: mcp, client };
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index++) {
    if (argv[index] === '--port') options.port = Number(argv[++index]);
    else if (argv[index] === '--python') options.python = argv[++index];
    else if (argv[index] === '--engine') options.engine = argv[++index];
    else if (argv[index] === '--help' || argv[index] === '-h') options.help = true;
    else throw new Error(`未知参数：${argv[index]}`);
  }
  return options;
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invoked === import.meta.url) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      console.log('用法：node harness.mjs [--port 0] [--python <python.exe>] [--engine <source_intake.py>]');
    } else {
      const harness = await startHarness(options);
      console.log(JSON.stringify({
        url: harness.url,
        demoProject: harness.demo.projectRoot,
        demoFiles: harness.demo.files,
        note: '仅本地验收：原生输入为固定演示数据，导入写入临时目录。Ctrl+C 结束并清理。',
      }, null, 2));
      const stop = async () => { await harness.close(); process.exit(0); };
      process.on('SIGINT', stop);
      process.on('SIGTERM', stop);
    }
  } catch (error) {
    console.error(`harness: ${error.message}`);
    process.exit(1);
  }
}
