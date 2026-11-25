import { access, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { z } from 'zod';
import { previewSchema, importSchema, buildRequest } from './contracts.mjs';
import { PreviewCache } from './preview-cache.mjs';
import { DEFAULT_ENGINE, detectPython, planRequest, applyPlan, readHistory, readRequirementContext } from './engine.mjs';

const result = value => ({ content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value });
const failure = error => ({ isError: true, content: [{ type: 'text', text: error.message || '操作失败' }] });
const readonly = { readOnlyHint: true, destructiveHint: false, openWorldHint: false };

export async function createUniversalServer({ python, engine, previewTtlMs, now } = {}) {
  const executable = python || await detectPython();
  const enginePath = engine || process.env.SOURCE_INTAKE_ENGINE || DEFAULT_ENGINE;
  const { version } = JSON.parse(await readFile(new URL('./package.json', import.meta.url), 'utf8'));
  const server = new McpServer({ name: 'source_intake', version });
  const cache = new PreviewCache({ ttlMs: previewTtlMs, now });
  const guard = fn => async args => { try { return result(await fn(args)); } catch (error) { return failure(error); } };

  server.registerTool('intake_status', {
    title: '源码需求导入健康检查', description: '检查本地运行时和内核。只读；通用MCP入口不依赖Codex、GUI、剪贴板或网络。',
    inputSchema: z.object({}).strict(), annotations: readonly,
  }, guard(async () => {
    await access(enginePath, constants.R_OK);
    await planRequest({ python: executable, engine: enginePath, request: { root: join(tmpdir(), 'source-intake-healthcheck'), sources: [] } });
    return { ok: true, version, transport: 'stdio', platform: process.platform,
      capabilities: ['plan', 'apply', 'history', 'requirement-context'],
      python: executable, engine: enginePath, ui_required: false, clipboard_access: false };
  }));

  server.registerTool('intake_plan', {
    title: '预览源码与需求材料导入',
    description: '按用户指定目录预览源码、图片文档或空白需求工作包，不写入项目。返回单次preview_id及逐项路径/冲突。默认生成独立需求目录。已有导入授权时可继续intake_apply；待补充或无效项须先修正。',
    inputSchema: previewSchema.extend({ sources: previewSchema.shape.sources.default([]), work_package: z.boolean().default(true) }),
    annotations: readonly,
  }, guard(async input => {
    const request = buildRequest(input);
    const plan = await planRequest({ python: executable, engine: enginePath, request });
    return { ...cache.create({ sessionId: input.session_id || null, request, plan }), plan };
  }));

  server.registerTool('intake_apply', {
    title: '执行已预览的导入',
    description: '仅用本连接缓存的preview_id执行导入。须有用户导入授权；默认跳过已有目标，用户明确要求替换时才用backup-replace。令牌单次使用，过期或来源/目标变化须重新预览，不自动重试写入。',
    inputSchema: importSchema, annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
  }, guard(async ({ preview_id, conflict }) => {
    const entry = cache.take(preview_id);
    const response = await applyPlan({ python: executable, engine: enginePath, request: entry.request, plan: entry.plan, conflict });
    if (response.exitCode === 2 || response.payload.ok === false && !response.payload.status) {
      throw new Error(response.payload.error?.message || '导入被拒绝，请重新预览。');
    }
    return response.payload;
  }));

  server.registerTool('intake_history', {
    title: '查看项目导入批次', description: '只读列出指定项目的导入历史、需求编号和批次状态。',
    inputSchema: z.object({ root: z.string().trim().min(1).max(4096) }).strict(), annotations: readonly,
  }, guard(({ root }) => readHistory({ python: executable, engine: enginePath, root })));

  server.registerTool('intake_context', {
    title: '读取需求与补充说明',
    description: '只读汇总需求目录的README、原始补充提示词和当前附件清单（包含后来手动添加的材料）。图片和文档需继续用当前智能体的图片/文档工具实际阅读，不能根据文件名猜需求。',
    inputSchema: z.object({ directory: z.string().trim().min(1).max(4096) }).strict(), annotations: readonly,
  }, guard(({ directory }) => readRequirementContext({ python: executable, directory })));

  server.registerPrompt('implement_requirement', {
    title: '开始实现已归档需求', description: '从需求目录接续修改任务，适用于不同编程智能体。',
    argsSchema: { directory: z.string().min(1).max(4096) },
  }, ({ directory }) => ({ messages: [{ role: 'user', content: { type: 'text', text:
    `请读取需求目录 ${JSON.stringify(directory)}。先调用 intake_context，实际查看全部相关截图/文档，结合修改说明和项目开发约定，再实现需求、运行适当验证并报告变化。遇到材料缺失或真实冲突应说明；不要仅凭文件名推测，也不要把材料归档误报成需求已实现。` } }] }));
  return server;
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invoked === import.meta.url) {
  const server = await createUniversalServer();
  await server.connect(new StdioServerTransport());
}
