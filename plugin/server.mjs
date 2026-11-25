import { previewSchema, importSchema, saveSettingsSchema, buildRequest } from './contracts.mjs';
export { buildRequest } from './contracts.mjs';
import { readFile, mkdtemp, writeFile, rm, realpath } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { DEFAULT_ENGINE, EngineError, applyPlan, detectPython, planRequest } from './engine.mjs';
import { NativeError, createNativeBridge } from './native.mjs';
import { PreviewCache, PreviewError } from './preview-cache.mjs';
import { SettingsStore } from './settings.mjs';

export const PANEL_URI = 'ui://source-intake/panel/v1';
export const mimeType = 'text/html;profile=mcp-app';
const ui = { prefersBorder: false, csp: {} };
const KINDS = ['csp', 'js', 'css', 'cls'];
const result = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value });
const toolError = error => ({ isError: true, content: [{ type: 'text', text: error?.message || '操作失败，请重试。' }] });

let builtHtml;
export async function panelHtml() {
  return builtHtml ??= buildHtml();
}

async function buildHtml() {
  const [html, bridge, panel] = await Promise.all(['panel.html', 'panel-bridge.js', 'panel.js']
    .map(name => readFile(new URL(name, import.meta.url), 'utf8')));
  const script = `${bridge}\n${panel}`.replace(/<\/script/gi, '<\\/script');
  return html
    .replace('<script src="/panel-bridge.js" defer></script>', '')
    .replace('<script src="/panel.js" defer></script>', `<script type="module">${script}</script>`);
}

function sanitizeNote(value) {
  return typeof value === 'string' && value.trim() ? value.trim().slice(0, 500) : undefined;
}

function sanitizePaths(value, limit = 200) {
  return Array.isArray(value)
    ? value.filter(item => typeof item === 'string' && item.trim()).slice(0, limit).map(item => item.trim())
    : [];
}

export async function createServer({
  python, engine, settingsDir, native, previewTtlMs, now, randomId, serverName = 'source_intake',
} = {}) {
  const pythonPath = python || await detectPython();
  const enginePath = engine || process.env.SOURCE_INTAKE_ENGINE || DEFAULT_ENGINE;
  const store = new SettingsStore(settingsDir ? { dir: settingsDir } : {});
  const nativeBridge = native || createNativeBridge({ python: pythonPath });
  const previews = new PreviewCache({ ttlMs: previewTtlMs, now, randomId });
  const screenshotDirs = new Set();
  const { version } = JSON.parse(await readFile(new URL('./package.json', import.meta.url), 'utf8'));
  const server = new McpServer({ name: serverName, version });
  const cleanupScreenshots = async () => {
    const temp = await realpath(tmpdir());
    await Promise.allSettled([...screenshotDirs].map(async path => {
      const actual = await realpath(path);
      if (dirname(actual).toLowerCase() !== temp.toLowerCase() || !basename(actual).startsWith('source-intake-screenshot-')) return;
      await rm(actual, { recursive: true, force: true });
      screenshotDirs.delete(path);
    }));
  };
  const close = server.close.bind(server);
  server.close = async () => {
    await close();
    await cleanupScreenshots();
  };
  server.server.onclose = () => { void cleanupScreenshots(); };

  server.registerTool('panel_screenshot', {
    title: '收集剪贴板截图',
    description: '仅响应用户点击读取剪贴板截图，暂存PNG供需求附件导入。',
    inputSchema: z.object({}).strict(),
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async () => {
    try {
      const captured = await nativeBridge.screenshot();
      if (!captured?.png_base64) return result({ canceled: true, note: captured?.note || '剪贴板中没有截图，可选择已保存的图片。' });
      if (captured.png_base64.length > 28_000_000) throw new NativeError('截图过大，请保存为图片后选择文件。');
      const png = Buffer.from(captured.png_base64, 'base64');
      if (!png.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex'))) throw new NativeError('截图格式无效。');
      const dir = await mkdtemp(join(tmpdir(), 'source-intake-screenshot-'));
      screenshotDirs.add(dir);
      const path = join(dir, `截图-${randomUUID().slice(0, 8)}.png`);
      await writeFile(path, png);
      return result({ canceled: false, path });
    } catch (error) { return toolError(error); }
  });

  server.registerResource('source-intake-panel', PANEL_URI, {
    title: '源码导入',
    mimeType,
    _meta: { ui },
  }, async () => ({
    contents: [{
      uri: PANEL_URI,
      mimeType,
      text: await panelHtml(),
      _meta: {
        ui,
        'openai/ui': { availableDisplayModes: ['fullscreen'], preferredDisplayMode: 'fullscreen' },
      },
    }],
  }));

  server.registerTool('open_panel', {
    title: '源码导入',
    description: '在 Codex 中打开源码导入面板，将用户提供的源码文件或粘贴代码导入本地项目并关联需求编号。只打开面板，不写入任何文件。',
    inputSchema: z.object({}).strict(),
    annotations: { readOnlyHint: true, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI }, 'openai/ui': { entrypoints: [{ type: 'global' }, { type: 'thread' }] } },
  }, async () => ({ content: [{ type: 'text', text: '源码导入面板已打开。预览确认后再由用户点击导入。' }] }));

  server.registerTool('panel_state', {
    title: '读取面板设置',
    description: '读取源码导入面板的最近项目目录与目录规则，合并旧桌面版设置中的导入偏好。只读，不读取剪贴板或项目文件。App-only；模型不可见。',
    inputSchema: z.object({}).strict(),
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async () => {
    try {
      const settings = await store.load();
      return result({
        settings,
        defaults: {
          profile: 'hisui',
          routes: {
            csp: 'frontend/csp',
            js: 'frontend/scripts/dhcnewpro/dhcemc',
            css: 'frontend/scripts/dhcnewpro/dhcemc/css',
            cls: 'backend',
          },
          cls_layout: 'package',
        },
        platform: process.platform,
      });
    } catch (error) { return toolError(error); }
  });

  server.registerTool('panel_choose_directory', {
    title: '选择项目目录',
    description: '响应用户点击，短暂显示系统目录选择框并返回所选目录；取消时返回 canceled=true。不创建目录、不写入文件。App-only；模型不可见。',
    inputSchema: z.object({ initial: z.string().max(4096).optional() }).strict(),
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async ({ initial }) => {
    try {
      const value = await nativeBridge.chooseDirectory(initial?.trim() || '');
      const canceled = value?.canceled !== false;
      return result({ canceled, path: canceled ? null : String(value.path) });
    } catch (error) { return toolError(error); }
  });

  server.registerTool('panel_choose_files', {
    title: '选择来源文件',
    description: '响应用户点击，短暂显示系统文件选择框并返回所选文件路径；取消时返回空列表。只选择，不读取内容、不写入文件。App-only；模型不可见。',
    inputSchema: z.object({ initial: z.string().max(4096).optional(), purpose: z.enum(['source', 'attachment']).optional() }).strict(),
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async ({ initial, purpose }) => {
    try {
      const value = await nativeBridge.chooseFiles(initial?.trim() || '', purpose);
      const canceled = value?.canceled !== false;
      return result({ canceled, paths: canceled ? [] : sanitizePaths(value.paths) });
    } catch (error) { return toolError(error); }
  });

  server.registerTool('panel_clipboard', {
    title: '收集剪贴板',
    description: '仅在用户点击“收集剪贴板”时调用：优先读取 Windows CF_HDROP 文件列表，否则读取 Unicode 文本。不启动监听、不保存剪贴板历史。App-only；模型不可见。',
    inputSchema: z.object({}).strict(),
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async () => {
    try {
      const value = await nativeBridge.clipboard();
      const note = sanitizeNote(value?.note);
      if (value?.kind === 'files') {
        const files = sanitizePaths(value.files);
        if (files.length) return result({ kind: 'files', files, ...(note ? { note } : {}) });
        return result({ kind: 'empty', ...(note ? { note } : {}) });
      }
      if (value?.kind === 'text' && typeof value.text === 'string' && value.text) {
        return result({ kind: 'text', text: value.text, ...(note ? { note } : {}) });
      }
      return result({ kind: 'empty', ...(note ? { note } : {}) });
    } catch (error) { return toolError(error); }
  });

  server.registerTool('panel_preview', {
    title: '预览导入',
    description: '校验项目目录、目录规则和来源清单，调用固定 Python 内核生成导入计划并缓存请求与计划，返回随机 preview_id。不创建项目目录、不写入文件。面板任何编辑后必须重新预览。App-only；模型不可见。',
    inputSchema: previewSchema,
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async input => {
    try {
      const request = buildRequest(input);
      const plan = await planRequest({ python: pythonPath, engine: enginePath, request });
      const token = previews.create({ sessionId: input.session_id || null, request, plan });
      return result({ ...token, plan });
    } catch (error) { return toolError(error); }
  });

  server.registerTool('panel_import', {
    title: '执行导入',
    description: '仅使用服务端缓存的预览请求与 expected-plan 执行导入；只接受 preview_id 和冲突策略，不接收页面提供的清单、路径或计划。令牌单次使用且会过期，来源或目标在预览后变化时内核拒绝覆盖。App-only；模型不可见。',
    inputSchema: importSchema,
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async ({ preview_id, conflict }) => {
    try {
      const entry = previews.take(preview_id);
      const { payload, exitCode } = await applyPlan({
        python: pythonPath, engine: enginePath, request: entry.request, plan: entry.plan, conflict,
      });
      if (exitCode === 2 || payload.ok === false && !payload.status) {
        throw new EngineError(payload.error?.message || '导入被内核拒绝，请重新预览。', {
          type: payload.error?.type || 'ImportError', payload,
        });
      }
      return result(payload);
    } catch (error) {
      return toolError(error);
    }
  });

  server.registerTool('panel_save_settings', {
    title: '保存目录规则',
    description: '保存用户选择的最近项目目录、项目名称、hisui/flat 布局与四类目录规则到个人设置文件；不覆盖旧桌面版设置，不保存需求内容或剪贴板历史。App-only；模型不可见。',
    inputSchema: saveSettingsSchema,
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    _meta: { ui: { resourceUri: PANEL_URI, visibility: ['app'] } },
  }, async input => {
    try {
      const settings = await store.saveProject({
        root: input.root,
        project_name: input.project_name || '',
        profile: input.profile,
        routes: input.routes ?? {},
        cls_layout: input.cls_layout,
      });
      return result({ settings });
    } catch (error) { return toolError(error); }
  });

  return server;
}

async function main() {
  const server = await createServer();
  await server.connect(new StdioServerTransport());
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invoked === import.meta.url) {
  main().catch(error => {
    console.error(`source-intake panel server: ${error.message}`);
    process.exit(1);
  });
}

export { EngineError, NativeError, PreviewError, KINDS, fileURLToPath };
