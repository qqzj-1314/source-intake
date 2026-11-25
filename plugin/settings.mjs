import { mkdir, readFile, realpath, rename, writeFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { access } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';

const MAX_RECENT = 8;
const KINDS = ['csp', 'js', 'css', 'cls'];

export function defaultSettingsDir(env = process.env) {
  const base = env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local');
  return join(base, 'SourceIntake');
}

function asText(value, limit) {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function normalizeRoutes(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const routes = {};
  for (const kind of KINDS) {
    if (typeof value[kind] === 'string') routes[kind] = value[kind];
  }
  return Object.keys(routes).length ? routes : null;
}

function normalizeProject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const profile = value.profile === 'flat' ? 'flat' : 'hisui';
  return {
    root: asText(value.root, 4096),
    project_name: asText(value.project_name, 200),
    profile,
    routes: normalizeRoutes(value.routes) || {},
    cls_layout: value.cls_layout === 'fullname' ? 'fullname' : 'package',
  };
}

async function readJson(path) {
  try {
    await access(path, constants.F_OK);
  } catch {
    return { value: null, error: null };
  }
  try {
    return { value: JSON.parse((await readFile(path, 'utf8')).replace(/^\uFEFF/, '')), error: null };
  } catch (error) {
    return { value: null, error: `读取 ${path} 失败：${error.message}` };
  }
}

export async function canonicalRoot(root) {
  const text = String(root || '').trim();
  if (!text) return '';
  const expanded = text.replace(/^~(?=$|[\\/])/, homedir());
  const absolute = resolve(expanded);
  try {
    return (await realpath(absolute)).toLowerCase();
  } catch {
    return absolute.toLowerCase();
  }
}

export class SettingsStore {
  constructor({ dir = defaultSettingsDir() } = {}) {
    this.dir = dir;
    this.panelPath = join(dir, 'panel-settings.json');
    this.legacyPath = join(dir, 'settings.json');
  }

  async load() {
    const [panelRead, legacyRead] = await Promise.all([readJson(this.panelPath), readJson(this.legacyPath)]);
    const warnings = [];
    if (panelRead.error) warnings.push(panelRead.error);
    if (legacyRead.error) warnings.push(legacyRead.error);
    const panel = panelRead.value && typeof panelRead.value === 'object' ? panelRead.value : {};
    const legacy = legacyRead.value && typeof legacyRead.value === 'object' ? legacyRead.value : {};

    const recent = [];
    const seen = new Set();
    for (const value of [...(Array.isArray(panel.recent_roots) ? panel.recent_roots : []),
      ...(Array.isArray(legacy.recent_roots) ? legacy.recent_roots : [])]) {
      if (typeof value !== 'string' || !value.trim()) continue;
      const key = value.trim().toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      recent.push(value.trim());
      if (recent.length >= MAX_RECENT) break;
    }

    const projects = {};
    for (const source of [legacy.projects, panel.projects]) {
      if (!source || typeof source !== 'object' || Array.isArray(source)) continue;
      for (const [key, value] of Object.entries(source)) {
        const project = normalizeProject(value);
        if (project) projects[String(key)] = project;
      }
    }
    return {
      recent_roots: recent,
      projects,
      paths: {
        panel: this.panelPath,
        legacy: legacyRead.value ? this.legacyPath : null,
        legacy_exists: Boolean(legacyRead.value),
      },
      warnings,
    };
  }

  async saveProject({ root, project_name = '', profile = 'hisui', routes = {}, cls_layout = 'package' }) {
    const displayRoot = resolve(String(root).trim().replace(/^~(?=$|[\\/])/, homedir()));
    if (!displayRoot || !isAbsolute(displayRoot)) throw new Error('项目目录无效。');
    const key = await canonicalRoot(displayRoot);
    const current = await readJson(this.panelPath);
    if (current.error) throw new Error(current.error);
    const panel = current.value && typeof current.value === 'object' ? current.value : {};
    const previous = panel.projects && typeof panel.projects === 'object' ? panel.projects : {};
    const recent = (Array.isArray(panel.recent_roots) ? panel.recent_roots : [])
      .filter(value => typeof value === 'string' && value.trim() && value.trim().toLowerCase() !== displayRoot.toLowerCase());
    recent.unshift(displayRoot);
    panel.version = 1;
    panel.recent_roots = recent.slice(0, MAX_RECENT);
    panel.projects = {
      ...previous,
      [key]: {
        root: displayRoot,
        project_name: asText(project_name, 200),
        profile: profile === 'flat' ? 'flat' : 'hisui',
        routes: normalizeRoutes(routes) || {},
        cls_layout: cls_layout === 'fullname' ? 'fullname' : 'package',
      },
    };
    await mkdir(dirname(this.panelPath), { recursive: true });
    const temporary = `${this.panelPath}.${process.pid}.${Date.now()}.tmp`;
    await writeFile(temporary, `${JSON.stringify(panel, null, 2)}\n`, 'utf8');
    await rename(temporary, this.panelPath);
    return this.load();
  }
}
