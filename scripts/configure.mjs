import { access, mkdir, readFile, writeFile, lstat } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { detectPython, DEFAULT_ENGINE } from '../plugin/engine.mjs';

const project = fileURLToPath(new URL('../', import.meta.url));
export const CLIENTS = ['generic', 'claude-code', 'qoder', 'trae', 'workbuddy', 'codex'];

export async function generateConfigs({ client = 'all', output = join(project, 'plugin/.local/clients'), python, node = process.execPath, force = false } = {}) {
  const selected = client === 'all' ? CLIENTS : [client];
  if (selected.some(name => !CLIENTS.includes(name))) throw new Error(`Unknown client: ${client}`);
  const pythonPath = python || await detectPython();
  await access(pythonPath);
  await access(node);
  const entry = join(project, 'plugin/launch.mjs');
  const spec = { command: node, args: [entry], env: { SOURCE_INTAKE_PYTHON: pythonPath, SOURCE_INTAKE_ENGINE: DEFAULT_ENGINE } };
  const config = { mcpServers: { source_intake: spec } };
  const directory = resolve(output);
  await mkdir(directory, { recursive: true });
  const files = [];
  for (const name of selected) {
    const path = join(directory, name === 'codex' ? 'codex.toml' : `${name}.json`);
    const body = name === 'codex'
      ? `[mcp_servers.source_intake]\ncommand = ${JSON.stringify(node)}\nargs = ${JSON.stringify([entry])}\n\n[mcp_servers.source_intake.env]\nSOURCE_INTAKE_PYTHON = ${JSON.stringify(pythonPath)}\nSOURCE_INTAKE_ENGINE = ${JSON.stringify(DEFAULT_ENGINE)}\n`
      : JSON.stringify(config, null, 2) + '\n';
    let previous;
    try {
      const info = await lstat(path);
      if (!info.isFile() || info.isSymbolicLink()) throw new Error(`Refusing non-regular export file: ${path}`);
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
    try { previous = await readFile(path, 'utf8'); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    if (previous !== undefined && previous !== body) {
      if (!force) throw new Error(`Refusing to overwrite ${path}; choose a new output directory or --force.`);
      await writeFile(`${path}.${Date.now()}.bak`, previous, { flag: 'wx' });
    }
    if (previous !== body) await writeFile(path, body, { flag: previous === undefined ? 'wx' : 'w' });
    files.push({ client: name, path, format: name === 'codex' ? 'toml-snippet' : 'mcpServers-json' });
  }
  return { ok: true, output: directory, files, server: spec, note: 'Generated configuration only. No existing client settings were edited. See docs/compatibility.md for installation.' };
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invoked === import.meta.url) {
  try {
    const options = {};
    for (let i = 2; i < process.argv.length; i++) {
      const arg = process.argv[i];
      if (arg === '--force') options.force = true;
      else if (['--client', '--output', '--python', '--node'].includes(arg)) {
        const value = process.argv[++i];
        if (!value || value.startsWith('--')) throw new Error(`Missing value for ${arg}`);
        options[arg.slice(2)] = value;
      } else throw new Error(`Unknown argument: ${arg}`);
    }
    console.log(JSON.stringify(await generateConfigs(options), null, 2));
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
