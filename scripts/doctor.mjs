import { readFile } from 'node:fs/promises';
import { Client } from '../plugin/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../plugin/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';

const option = process.argv.indexOf('--config');
let client;
try {
  if (option < 0 || !process.argv[option + 1]) throw new Error('Usage: node scripts/doctor.mjs --config <generated-client.json>');
  const document = JSON.parse(await readFile(process.argv[option + 1], 'utf8'));
  const spec = document.mcpServers?.source_intake;
  if (!spec?.command || !Array.isArray(spec.args)) throw new Error('Missing source_intake MCP configuration');
  client = new Client({ name: 'source-intake-doctor', version: '1.0.0' });
  const transport = new StdioClientTransport({ command: spec.command, args: spec.args, env: { ...process.env, ...spec.env }, stderr: 'pipe' });
  let diagnostics = '';
  transport.stderr?.on('data', data => { diagnostics = (diagnostics + data.toString()).slice(-1000); });
  await client.connect(transport, { timeout: 15000 });
  const { tools } = await client.listTools();
  const response = await client.callTool({ name: 'intake_status', arguments: {} });
  if (response.isError) throw new Error(response.content?.[0]?.text || diagnostics || 'Health check failed');
  console.log(JSON.stringify({ ok: true, protocol: 'MCP stdio', tools: tools.map(tool => tool.name), status: response.structuredContent }, null, 2));
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
} finally { await client?.close().catch(() => {}); }
