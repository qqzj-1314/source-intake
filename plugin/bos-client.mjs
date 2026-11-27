import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { z } from 'zod';

export const BOS_ENDPOINT = 'https://costrict.imedway.com:1443/mediway-mcphub/mcp/6fc63f35-ebef-4ce9-8e3c-0f1768ed0d7b';
export const BOS_TOOL_NAME = 'post_imedway_ylxt_bosdata';
export const BOS_DEFAULT_EXPLAIN = '源码导入插件查询需求';
export const BOS_QUERY_FIELDS = Object.freeze([
  'cgroup', 'content', 'cuser', 'date', 'date2', 'datetype', 'explain', 'id', 'mod',
  'product', 'sgroup', 'source', 'status', 'suser', 'tgroup', 'title', 'tuser', 'type',
]);
export const BOS_ERRORS = Object.freeze({
  NOT_CONFIGURED: 'BOS 未配置：请仅在服务端设置 IMEDWAY_GATEWAY_KEY 和 IMEDWAY_BOS_TOKEN。',
  FILTER_REQUIRED: 'BOS 查询必须提供 id，或至少一个按组或按人条件。',
  INVALID_ID: 'BOS id 必须为整数。',
  INVALID_BODY: 'BOS body 必须是可序列化且不超过 20000 字节的 JSON 对象。',
  AUTH: 'BOS 认证失败，请检查服务端凭据。',
  TIMEOUT: 'BOS 请求超时，请稍后重试。',
  CANCELED: 'BOS 查询已取消。',
  CONNECTION: 'BOS 连接失败，请检查网络或服务状态。',
  CONTRACT: 'BOS 工具合约不兼容，未执行查询。',
  QUERY: 'BOS 查询失败，请稍后重试。',
  REJECTED: 'BOS 上游拒绝了只读查询。',
  BUSINESS: 'BOS 业务查询失败（code 非 200）。',
  MALFORMED: 'BOS 响应格式无效。',
  TOO_LARGE: 'BOS 响应超过大小限制。',
});

const SCOPE_FIELDS = ['cgroup', 'cuser', 'sgroup', 'suser', 'tgroup', 'tuser'];
const queryText = (max = 500) => z.string().max(max).default('');
export const bosQuerySchema = z.object({
  cgroup: queryText(),
  content: queryText(20_000),
  cuser: queryText(),
  date: queryText(10).describe('起始日期 yyyy-MM-dd；缺省时上游默认为当日。'),
  date2: queryText(10).describe('截止日期 yyyy-MM-dd；缺省时上游默认为 date 次日。'),
  datetype: queryText(20),
  explain: z.string().max(4000).default(BOS_DEFAULT_EXPLAIN).describe('上游必填日志元字段，使用自然中文说明调用用途，不是字段说明开关。'),
  id: queryText(200).describe('数字需求序号；有值时忽略其他业务筛选条件。'),
  mod: queryText(),
  product: queryText(),
  sgroup: queryText(),
  source: queryText(100),
  status: queryText(100),
  suser: queryText(),
  tgroup: queryText(),
  title: queryText(500),
  tuser: queryText(),
  type: queryText(100),
  body: z.record(z.string(), z.unknown()).optional(),
  limit: z.number().int().min(1).max(50).default(20),
}).strict();

export class BosError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'BosError';
    this.code = code;
  }
}

export function normalizeBosRequirement(record) {
  if (!record || typeof record !== 'object' || Array.isArray(record)) throw new Error('BOS 结果记录格式无效。');
  const id = record['需求号'];
  if (typeof id !== 'number' || !Number.isFinite(id)) throw new Error('BOS 结果核心字段无效：需求号。');
  for (const field of ['标题', '描述', '操作时间']) {
    if (typeof record[field] !== 'string') throw new Error(`BOS 结果核心字段无效：${field}。`);
  }
  if (!record['标题'].trim()) throw new Error('BOS 结果核心字段无效：标题。');
  if (!record['操作时间'].trim()) throw new Error('BOS 结果核心字段无效：操作时间。');
  return {
    system: 'BOS',
    id: String(id),
    title: record['标题'],
    description: record['描述'],
    updated_at: record['操作时间'],
    url: '',
  };
}

const fail = (code) => { throw new BosError(code, BOS_ERRORS[code]); };
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function normalizeQuery(input) {
  const query = bosQuerySchema.parse(input ?? {});
  const id = query.id.trim();
  if (id && !/^-?\d+$/.test(id)) fail('INVALID_ID');
  if (!id && !SCOPE_FIELDS.some(field => query[field].trim())) fail('FILTER_REQUIRED');
  const explain = query.explain.trim() || BOS_DEFAULT_EXPLAIN;
  const args = Object.fromEntries(BOS_QUERY_FIELDS.map(field => [
    field,
    field === 'explain' ? explain : id && field !== 'id' ? '' : query[field].trim(),
  ]));
  if (query.body !== undefined) {
    let size;
    try { size = Buffer.byteLength(JSON.stringify(query.body)); }
    catch { fail('INVALID_BODY'); }
    if (size > 20_000) fail('INVALID_BODY');
    args.body = query.body;
  }
  return { args, limit: query.limit };
}

function normalizeTransportError(error, fallback) {
  if (error instanceof BosError) throw error;
  const code = error?.code ?? error?.status ?? error?.cause?.code ?? error?.cause?.status;
  if (error?.name === 'UnauthorizedError' || code === 401 || code === 403 || /\b(?:401|403|unauthorized|forbidden)\b/i.test(error?.message || '')) fail('AUTH');
  if (error?.name === 'AbortError') fail('CANCELED');
  if (code === -32001 || code === 'ETIMEDOUT' || error?.name === 'TimeoutError') fail('TIMEOUT');
  fail(fallback);
}

function verifyToolContract(listed) {
  const tool = listed?.tools?.find(item => item?.name === BOS_TOOL_NAME);
  const schema = tool?.inputSchema;
  if (!tool || schema?.type !== 'object' || !Array.isArray(schema.required) ||
      !BOS_QUERY_FIELDS.every(field => schema.required.includes(field) && schema.properties?.[field]?.type === 'string')) {
    fail('CONTRACT');
  }
}

function sanitizeValue(value, state, depth = 0) {
  if (typeof value === 'string') {
    if (value.length > 20_000) {
      state.truncated = true;
      return value.slice(0, 20_000);
    }
    return value;
  }
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) fail('MALFORMED');
    return value;
  }
  if (depth >= 6 || typeof value !== 'object') fail('MALFORMED');
  if (Array.isArray(value)) {
    if (value.length > 100) state.truncated = true;
    return value.slice(0, 100).map(item => sanitizeValue(item, state, depth + 1));
  }
  const keys = Object.keys(value);
  if (keys.length > 100) state.truncated = true;
  const entries = [];
  for (const key of keys.slice(0, 100)) {
    if (key.length > 100) {
      state.truncated = true;
      continue;
    }
    entries.push([key, sanitizeValue(value[key], state, depth + 1)]);
  }
  return Object.fromEntries(entries);
}

function parseResponse(response, limit) {
  if (response?.isError) fail('REJECTED');
  if (!Array.isArray(response?.content) || response.content.length !== 1 ||
      response.content[0]?.type !== 'text' || typeof response.content[0].text !== 'string' ||
      response.content[0].text.length > 2_000_000) {
    fail(response?.content?.[0]?.text?.length > 2_000_000 ? 'TOO_LARGE' : 'MALFORMED');
  }
  let payload;
  try { payload = JSON.parse(response.content[0].text); }
  catch { fail('MALFORMED'); }
  if (!isRecord(payload) || typeof payload.code !== 'number') fail('MALFORMED');
  if (payload.code !== 200) fail('BUSINESS');
  if (typeof payload.msg !== 'string' || !Array.isArray(payload.data)) fail('MALFORMED');
  const state = { truncated: payload.data.length > limit };
  const items = payload.data.slice(0, limit).map(record => {
    if (!isRecord(record)) fail('MALFORMED');
    const sanitized = sanitizeValue(record, state);
    if (Buffer.byteLength(JSON.stringify(sanitized)) > 200_000) fail('TOO_LARGE');
    return sanitized;
  });
  return { items, count: items.length, total: payload.data.length, truncated: state.truncated };
}

export function createBosClient({
  env = process.env,
  endpoint = BOS_ENDPOINT,
  timeoutMs = 10_000,
  transportFactory,
  clientFactory,
} = {}) {
  let client = null;
  let connecting = null;
  let connected = false;
  const credentials = () => ({
    gateway: env.IMEDWAY_GATEWAY_KEY || '',
    token: env.IMEDWAY_BOS_TOKEN || '',
  });
  const reset = target => {
    if (client && client !== target) return;
    client = null;
    connected = false;
  };
  const closeClient = async target => {
    reset(target);
    await target?.close().catch(() => {});
  };
  const connect = () => {
    if (client && connected) return Promise.resolve(client);
    if (connecting) return connecting;
    connecting = (async () => {
      const { gateway, token } = credentials();
      if (!gateway || !token) fail('NOT_CONFIGURED');
      const headers = {
        Authorization: `Bearer ${gateway}`,
        'X-Gateway-Upstream-BOS-MCP-TOKEN': token,
      };
      const transport = transportFactory
        ? transportFactory({ url: new URL(endpoint), headers })
        : new StreamableHTTPClientTransport(new URL(endpoint), { requestInit: { headers } });
      const nextClient = clientFactory
        ? clientFactory()
        : new Client({ name: 'source-intake-bos', version: '1' });
      nextClient.onclose = () => reset(nextClient);
      try {
        await nextClient.connect(transport, { timeout: timeoutMs });
        const listed = await nextClient.listTools(undefined, { timeout: timeoutMs });
        verifyToolContract(listed);
      } catch (error) {
        await closeClient(nextClient);
        normalizeTransportError(error, 'CONNECTION');
      }
      client = nextClient;
      connected = true;
      return nextClient;
    })();
    return connecting.finally(() => { connecting = null; });
  };
  return {
    status() {
      const { gateway, token } = credentials();
      return { configured: Boolean(gateway && token), connected };
    },
    async query(input, { signal } = {}) {
      const { args, limit } = normalizeQuery(input);
      if (signal?.aborted) fail('CANCELED');
      const activeClient = await connect();
      if (signal?.aborted) fail('CANCELED');
      let response;
      try {
        response = await activeClient.callTool({ name: BOS_TOOL_NAME, arguments: args }, undefined, { timeout: timeoutMs, signal });
      } catch (error) {
        await closeClient(activeClient);
        normalizeTransportError(error, 'QUERY');
      }
      return parseResponse(response, limit);
    },
    async close() {
      if (connecting) await connecting.catch(() => {});
      const activeClient = client;
      client = null;
      connected = false;
      await activeClient?.close().catch(() => {});
    },
  };
}
