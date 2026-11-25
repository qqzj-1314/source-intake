import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { Client } from '../plugin/node_modules/@modelcontextprotocol/sdk/dist/esm/client/index.js';
import { StdioClientTransport } from '../plugin/node_modules/@modelcontextprotocol/sdk/dist/esm/client/stdio.js';

export async function runDoctor(configPath) {
  if (!configPath) throw new Error('Usage: node plugin/doctor-launch.mjs --config <generated-client.json>');
  const document = JSON.parse(await readFile(configPath, 'utf8'));
  const spec = document.mcpServers?.source_intake;
  if (!spec?.command || !Array.isArray(spec.args)) throw new Error('Missing source_intake MCP configuration');
  const client = new Client({ name: 'source-intake-doctor', version: '1.0.0' });
  const transport = new StdioClientTransport({ command: spec.command, args: spec.args, env: { ...process.env, ...spec.env }, stderr: 'pipe' });
  let diagnostics = '';
  transport.stderr?.on('data', data => { diagnostics = (diagnostics + data.toString()).slice(-1000); });
  try {
    await client.connect(transport, { timeout: 15000 });
    const { tools } = await client.listTools();
    const response = await client.callTool({ name: 'intake_status', arguments: {} });
    if (response.isError) throw new Error(response.content?.[0]?.text || diagnostics || 'Health check failed');
    return { ok: true, protocol: 'MCP stdio', tools: tools.map(tool => tool.name), status: response.structuredContent };
  } finally { await client.close().catch(() => {}); }
}

export async function doctorCLI() {
  try {
    const option = process.argv.indexOf('--config');
    console.log(JSON.stringify(await runDoctor(option < 0 ? null : process.argv[option + 1]), null, 2));
  } catch (error) {
    console.error(JSON.stringify({ ok: false, error: error.message }));
    process.exitCode = 1;
  }
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invoked === import.meta.url) await doctorCLI();
