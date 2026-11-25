import { access, mkdir, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { DEFAULT_ENGINE, detectPython } from './engine.mjs';

const pluginDir = fileURLToPath(new URL('.', import.meta.url));
const serverEntry = fileURLToPath(new URL('./server.mjs', import.meta.url));
export const DEFAULT_MARKETPLACE_ROOT = fileURLToPath(new URL('./.local/marketplace/', import.meta.url));

async function writeJson(path, value) {
  await mkdir(join(path, '..'), { recursive: true });
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

async function assertFile(path, label) {
  try {
    await access(path, constants.F_OK);
  } catch {
    throw new Error(`${label}不存在：${path}`);
  }
}

export async function preparePlugin({
  root = DEFAULT_MARKETPLACE_ROOT,
  python,
  node = process.execPath,
  engine = DEFAULT_ENGINE,
} = {}) {
  const marketplaceRoot = resolve(root);
  const pythonPath = python || await detectPython();
  const nodePath = resolve(node);
  const enginePath = resolve(engine);
  await Promise.all([assertFile(pythonPath, 'Python 解释器'), assertFile(nodePath, 'Node 可执行文件'), assertFile(enginePath, '源码导入内核')]);
  await assertFile(serverEntry, '面板服务端');

  const pluginRoot = join(marketplaceRoot, 'plugins', 'source-intake');
  const catalogPath = join(marketplaceRoot, '.agents', 'plugins', 'marketplace.json');
  await Promise.all([
    mkdir(join(marketplaceRoot, '.agents', 'plugins'), { recursive: true }),
    mkdir(join(pluginRoot, '.codex-plugin'), { recursive: true }),
  ]);

  await writeJson(join(pluginRoot, '.codex-plugin', 'plugin.json'), {
    name: 'source-intake',
    version: '0.1.0',
    description: 'Codex 内嵌源码导入面板：预览后把源码文件或粘贴代码导入本地项目，并关联需求编号。',
    author: { name: 'source-intake' },
    mcpServers: './.mcp.json',
    interface: {
      displayName: '源码导入',
      shortDescription: '预览后导入源码，保留原始快照并关联需求编号。',
      longDescription: '在 Codex 中打开中文源码导入面板：选择或新建项目目录，关联需求编号，收集剪贴板或文件来源，按 hisui/flat 规则预览后导入，并在项目 .source-intake 中保留批次、原始快照与需求信息。安装与信任由 Codex 控制。',
      developerName: 'source-intake',
      category: 'Developer Tools',
      capabilities: ['Read', 'Write'],
      brandColor: '#3B6E5A',
    },
  });

  await writeJson(join(pluginRoot, '.mcp.json'), {
    mcpServers: {
      source_intake: {
        command: nodePath,
        args: [serverEntry],
        env: {
          SOURCE_INTAKE_PYTHON: pythonPath,
          SOURCE_INTAKE_ENGINE: enginePath,
        },
        startup_timeout_sec: 20,
      },
    },
  });

  await writeJson(catalogPath, {
    name: 'source-intake-local',
    interface: { displayName: '源码导入（本地）' },
    plugins: [{
      name: 'source-intake',
      source: { source: 'local', path: './plugins/source-intake' },
      policy: { installation: 'AVAILABLE', authentication: 'ON_INSTALL' },
      category: 'Developer Tools',
    }],
  });

  return {
    root: marketplaceRoot,
    plugin: pluginRoot,
    selector: 'source-intake@source-intake-local',
    python: pythonPath,
    engine: enginePath,
    node: nodePath,
    server: serverEntry,
    marketplace: catalogPath,
    pluginJson: join(pluginRoot, '.codex-plugin', 'plugin.json'),
    mcpJson: join(pluginRoot, '.mcp.json'),
    cwd: pluginDir,
  };
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index++) {
    const arg = argv[index];
    if (arg === '--root') options.root = argv[++index];
    else if (arg === '--python') options.python = argv[++index];
    else if (arg === '--node') options.node = argv[++index];
    else if (arg === '--engine') options.engine = argv[++index];
    else if (arg === '--help' || arg === '-h') options.help = true;
    else throw new Error(`未知参数：${arg}`);
  }
  return options;
}

const invoked = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : '';
if (invoked === import.meta.url) {
  try {
    const options = parseArgs(process.argv.slice(2));
    if (options.help) {
      console.log('用法：node prepare-plugin.mjs [--root <本地市场目录>] [--python <python.exe>] [--engine <source_intake.py>] [--node <node.exe>]');
    } else {
      console.log(JSON.stringify(await preparePlugin(options), null, 2));
    }
  } catch (error) {
    console.error(`prepare-plugin: ${error.message}`);
    process.exit(1);
  }
}
