import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import {
  BOS_DEFAULT_EXPLAIN, BOS_ENDPOINT, BOS_ERRORS, BOS_QUERY_FIELDS, BOS_TOOL_NAME, createBosClient, normalizeBosRequirement,
} from '../bos-client.mjs';
import { buildRequest, previewSchema } from '../contracts.mjs';
import { createUniversalServer } from '../universal-server.mjs';
import { DEFAULT_ENGINE, PYTHON, fixture } from './helpers.mjs';

const ENV = { IMEDWAY_GATEWAY_KEY: 'gateway-test-secret', IMEDWAY_BOS_TOKEN: 'token-test-secret' };
const SECRETS = ['gateway-test-secret', 'token-test-secret', 'upstream-sensitive-body'];
const envelope = payload => ({ content: [{ type: 'text', text: JSON.stringify(payload) }] });

function syntheticClient({ env = ENV, response, connectError, callError } = {}) {
  const calls = { connect: [], queries: [], closed: 0, transport: null };
  const sdkClient = {
    async connect(transport, options) {
      calls.connect.push({ transport, options });
      if (connectError) throw connectError;
    },
    async listTools(params, options) {
      calls.listTools = { params, options };
      return { tools: [{
        name: BOS_TOOL_NAME,
        inputSchema: {
          type: 'object',
          required: [...BOS_QUERY_FIELDS],
          properties: Object.fromEntries(BOS_QUERY_FIELDS.map(field => [field, { type: 'string' }])),
        },
      }] };
    },
    async callTool(request, schema, options) {
      calls.queries.push({ request, options });
      if (callError) throw callError;
      return (typeof response === 'function' ? response(request) : response) ?? envelope({ code: 200, msg: '合成空结果', data: [] });
    },
    async close() { calls.closed += 1; },
  };
  const bos = createBosClient({
    env,
    timeoutMs: 1234,
    transportFactory(options) {
      calls.transport = options;
      return { synthetic: true };
    },
    clientFactory() { return sdkClient; },
  });
  return { bos, calls };
}

function rejectsWith(promise, code) {
  return assert.rejects(promise, error => {
    assert.equal(error.name, 'BosError');
    assert.equal(error.code, code);
    assert.equal(error.message, BOS_ERRORS[code]);
    for (const secret of SECRETS) assert.ok(!error.message.includes(secret));
    return true;
  });
}

test('BOS stays optional and status does not confuse configured with connected', async () => {
  let connections = 0;
  const bos = createBosClient({
    env: {},
    transportFactory() { connections += 1; return {}; },
    clientFactory() { return {}; },
  });
  assert.deepEqual(bos.status(), { configured: false, connected: false });
  await rejectsWith(bos.query({ id: '-1' }), 'NOT_CONFIGURED');
  assert.equal(connections, 0);

  const configured = syntheticClient();
  assert.deepEqual(configured.bos.status(), { configured: true, connected: false });
  assert.equal(configured.calls.connect.length, 0);
  const empty = await configured.bos.query({ id: '-999999999' });
  assert.deepEqual(empty, { items: [], count: 0, total: 0, truncated: false });
  assert.deepEqual(configured.bos.status(), { configured: true, connected: true });
  await configured.bos.close();
  assert.deepEqual(configured.bos.status(), { configured: true, connected: false });
});

test('BOS fills required strings, enforces scoped queries and applies result limits', async () => {
  const longTitle = '合成字段'.repeat(6000);
  const { bos, calls } = syntheticClient({
    response: envelope({ code: 200, msg: '合成成功', data: [
      { custom_field: '保留未知字段', title: longTitle },
      { custom_field: '被数量限制' },
    ] }),
  });
  const result = await bos.query({ cgroup: ' 合成组 ', title: ' 合成标题 ', body: { synthetic: true }, limit: 1 });
  const { request, options } = calls.queries[0];
  assert.equal(request.name, BOS_TOOL_NAME);
  assert.equal(options.timeout, 1234);
  for (const field of BOS_QUERY_FIELDS) assert.equal(typeof request.arguments[field], 'string', field);
  assert.equal(request.arguments.cgroup, '合成组');
  assert.equal(request.arguments.title, '合成标题');
  assert.equal(request.arguments.content, '');
  assert.equal(request.arguments.explain, BOS_DEFAULT_EXPLAIN);
  assert.deepEqual(request.arguments.body, { synthetic: true });
  assert.equal(result.count, 1);
  assert.equal(result.total, 2);
  assert.equal(result.truncated, true);
  assert.equal(result.items[0].custom_field, '保留未知字段');
  assert.equal(result.items[0].title.length, 20_000);

  await bos.query({ id: ' -999999999 ', cgroup: '忽略', title: '忽略', date: '2026-01-01' });
  const byId = calls.queries[1].request.arguments;
  assert.equal(byId.id, '-999999999');
  assert.equal(byId.explain, BOS_DEFAULT_EXPLAIN);
  assert.ok(!('body' in byId));
  for (const field of BOS_QUERY_FIELDS.filter(field => field !== 'id' && field !== 'explain')) assert.equal(byId[field], '', field);
  await rejectsWith(bos.query({ id: 'BOS-1' }), 'INVALID_ID');
  await rejectsWith(bos.query({ title: '无范围条件' }), 'FILTER_REQUIRED');
  await assert.rejects(bos.query({ id: '-1', limit: 51 }));
  await rejectsWith(bos.query({ id: '-1', body: { text: 'x'.repeat(20_001) } }), 'INVALID_BODY');
  assert.equal(calls.queries.length, 2);
  await bos.close();
});

test('BOS sends credentials only in transport headers and preserves generic successful fields', async () => {
  const { bos, calls } = syntheticClient({
    response: envelope({ code: 200, msg: '合成成功', data: [{ unknown_remote_key: 42, nested: { ok: true } }] }),
  });
  const result = await bos.query({ suser: '合成用户' });
  assert.equal(calls.transport.url.href, BOS_ENDPOINT);
  assert.equal(calls.transport.headers.Authorization, 'Bearer gateway-test-secret');
  assert.equal(calls.transport.headers['X-Gateway-Upstream-BOS-MCP-TOKEN'], 'token-test-secret');
  assert.equal(calls.connect[0].options.timeout, 1234);
  assert.deepEqual(result.items, [{ unknown_remote_key: 42, nested: { ok: true } }]);
  const exposed = JSON.stringify({ status: bos.status(), result });
  for (const secret of SECRETS) assert.ok(!exposed.includes(secret));
  await bos.close();
});

test('BOS converts HTTP authentication failures without upstream details', async () => {
  const error = Object.assign(new Error('HTTP 401 gateway-test-secret token-test-secret upstream-sensitive-body'), { code: 401 });
  const { bos, calls } = syntheticClient({ connectError: error });
  await rejectsWith(bos.query({ id: '-1' }), 'AUTH');
  assert.equal(calls.queries.length, 0);
  assert.deepEqual(bos.status(), { configured: true, connected: false });
  await bos.close();
});

test('BOS converts business errors without exposing msg or data', async () => {
  const { bos } = syntheticClient({
    response: envelope({ code: 500, msg: 'upstream-sensitive-body gateway-test-secret', data: [{ token: 'token-test-secret' }] }),
  });
  await rejectsWith(bos.query({ id: '-1' }), 'BUSINESS');
  await bos.close();
});

test('BOS rejects malformed JSON and malformed payloads with fixed errors', async t => {
  await t.test('bad JSON', async () => {
    const { bos } = syntheticClient({ response: { content: [{ type: 'text', text: '{bad upstream-sensitive-body' }] } });
    await rejectsWith(bos.query({ id: '-1' }), 'MALFORMED');
    await bos.close();
  });
  await t.test('non-array data', async () => {
    const { bos } = syntheticClient({ response: envelope({ code: 200, msg: 'upstream-sensitive-body', data: {} }) });
    await rejectsWith(bos.query({ id: '-1' }), 'MALFORMED');
    await bos.close();
  });
  await t.test('upstream MCP rejection', async () => {
    const { bos } = syntheticClient({ response: { isError: true, content: [{ type: 'text', text: 'upstream-sensitive-body' }] } });
    await rejectsWith(bos.query({ id: '-1' }), 'REJECTED');
    await bos.close();
  });
});

test('BOS converts SDK request timeouts without leaking details', async () => {
  const error = Object.assign(new Error('timeout upstream-sensitive-body token-test-secret'), { code: -32001 });
  const { bos, calls } = syntheticClient({ callError: error });
  await rejectsWith(bos.query({ id: '-1' }), 'TIMEOUT');
  assert.equal(calls.queries[0].options.timeout, 1234);
  assert.deepEqual(bos.status(), { configured: true, connected: false });
  await bos.close();
});

test('BOS requirement normalization uses confirmed Chinese keys and rejects missing core fields', () => {
  const normalized = normalizeBosRequirement({
    '需求号': 1001,
    '标题': '合成标题 <script>globalThis.executed=true</script>',
    '描述': '合成描述',
    '操作时间': '2026-01-02 03:04:05',
    '创建时间': '2026-01-01 01:02:03',
    '附件': [],
  });
  assert.deepEqual(normalized, {
    system: 'BOS', id: '1001', title: '合成标题 <script>globalThis.executed=true</script>',
    description: '合成描述', updated_at: '2026-01-02 03:04:05', url: '',
  });
  for (const field of ['需求号', '标题', '描述', '操作时间']) {
    const record = { '需求号': 1001, '标题': '合成标题', '描述': '', '操作时间': '2026-01-02 03:04:05' };
    delete record[field];
    assert.throws(() => normalizeBosRequirement(record), new RegExp(field));
  }
});

test('request contract preserves manual defaults and accepts BOS updated_at', () => {
  const base = { root: '.', sources: [] };
  const manual = buildRequest(previewSchema.parse(base));
  assert.equal(manual.requirement.system, 'manual');
  assert.equal(manual.requirement.updated_at, '');
  const bos = buildRequest(previewSchema.parse({
    ...base,
    requirement: { system: 'BOS', id: 'SYNTHETIC', updated_at: ' 2026-01-02T03:04:05Z ' },
  }));
  assert.equal(bos.requirement.system, 'BOS');
  assert.equal(bos.requirement.updated_at, '2026-01-02T03:04:05Z');
});

test('panel and universal BOS tools use injected clients without startup calls', async t => {
  const calls = [];
  const bos = {
    status() { calls.push(['status']); return { configured: true, connected: false }; },
    async query(input, options) { calls.push(['query', input.id, Boolean(options?.signal)]); return { items: [], count: 0, total: 0, truncated: false }; },
    async close() {},
  };
  const panel = await fixture(t, { server: { bos } });
  assert.equal(calls.length, 0);
  assert.deepEqual((await panel.call('panel_bos_status')).structuredContent, { configured: true, connected: false });
  const panelQuery = await panel.client.callTool({
    name: 'panel_bos_query', arguments: { id: '1' },
  }, undefined, { signal: new AbortController().signal });
  assert.equal(panelQuery.isError, undefined);
  assert.equal(panelQuery.structuredContent.count, 0);

  const server = await createUniversalServer({ python: PYTHON, engine: DEFAULT_ENGINE, bos });
  const client = new Client({ name: 'synthetic-universal-bos-test', version: '1' });
  const [serverTransport, clientTransport] = InMemoryTransport.createLinkedPair();
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  t.after(async () => {
    await client.close();
    await server.close();
  });
  assert.equal(calls.length, 2);
  const status = await client.callTool({ name: 'intake_bos_status', arguments: {} });
  assert.deepEqual(status.structuredContent, { configured: true, connected: false });
  const query = await client.callTool({
    name: 'intake_bos_query', arguments: { id: '2' },
  }, undefined, { signal: new AbortController().signal });
  assert.equal(query.isError, undefined);
  assert.equal(query.structuredContent.count, 0);
  assert.deepEqual(calls, [
    ['status'], ['query', '1', true], ['status'], ['query', '2', true],
  ]);
});
