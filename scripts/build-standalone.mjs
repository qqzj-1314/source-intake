import { createRequire } from 'node:module';
import { access, mkdir, readFile, readdir, realpath, writeFile, lstat } from 'node:fs/promises';
import { resolve, join, relative, dirname, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { builtinModules } from 'node:module';
import { normalizedHash, binaryHash } from '../plugin/runtime-paths.mjs';

const project = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(new URL('../plugin/package.json', import.meta.url));
const { build, version: esbuildVersion } = require('esbuild');
const out = join(project, 'bin');
const packageJson = JSON.parse(await readFile(join(project, 'plugin/package.json'), 'utf8'));
await mkdir(out, { recursive: true });
if ((await lstat(out)).isSymbolicLink() || dirname(await realpath(out)) !== await realpath(project)) throw new Error('Refusing output directory outside project');

const entries = {};
const sources = {};
const dependencyDirectories = new Set();
for (const [kind, source, name] of [['server', 'plugin/universal-server.mjs', 'universal-server.mjs'], ['doctor', 'scripts/doctor.mjs', 'doctor.mjs']]) {
  const output = await build({
    absWorkingDir: project, entryPoints: [source], outfile: join(out, name), bundle: true,
    format: 'esm', platform: 'node', target: ['node22.19'], charset: 'utf8',
    legalComments: 'linked', metafile: true, logLevel: 'warning',
    banner: { js: "import { createRequire as __sourceIntakeRequire } from 'node:module'; const require = __sourceIntakeRequire(import.meta.url);" },
  });
  for (const data of Object.values(output.metafile.outputs)) {
    for (const imported of data.imports || []) {
      if (imported.external && !imported.path.startsWith('node:') && !builtinModules.includes(imported.path)) throw new Error(`Non-bundled dependency: ${imported.path}`);
    }
  }
  for (const input of Object.keys(output.metafile.inputs)) {
    const normalized = input.replaceAll('\\', '/');
    const marker = '/node_modules/';
    const start = ('/' + normalized).lastIndexOf(marker);
    if (start >= 0) {
      const prefix = ('/' + normalized).slice(1, start + marker.length - 1);
      const rest = ('/' + normalized).slice(start + marker.length).split('/');
      dependencyDirectories.add(join(project, prefix, ...rest.slice(0, rest[0].startsWith('@') ? 2 : 1)));
    } else {
      const path = resolve(project, input);
      const rel = relative(project, path).replaceAll('\\', '/');
      if (rel.startsWith('../') || isAbsolute(rel)) throw new Error(`Source outside project: ${rel}`);
      sources[rel] = normalizedHash(await readFile(path));
    }
  }
  entries[kind] = { path: 'bin/' + name, sha256: binaryHash(await readFile(join(out, name))) };
}
for (const path of ['plugin/package.json', 'plugin/package-lock.json']) sources[path] = normalizedHash(await readFile(join(project, path)));
const licenses = ['# Bundled third-party notices', '', 'These notices apply to bundled dependencies, not a license grant for the application.', ''];
const dependencies = [];
for (const directory of [...dependencyDirectories].sort()) {
  const pkg = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
  dependencies.push({ name: pkg.name, version: pkg.version, license: pkg.license || 'See package notices' });
  licenses.push(`## ${pkg.name} ${pkg.version}`, '', `Declared license: ${typeof pkg.license === 'string' ? pkg.license : JSON.stringify(pkg.license)}`, '');
  const files = (await readdir(directory)).filter(name => /^(licen[sc]e|notice)(\..*)?$/i.test(name)).sort();
  if (!files.length) throw new Error(`Missing dependency license text: ${pkg.name}`);
  for (const name of files) licenses.push(`### ${name}`, '', await readFile(join(directory, name), 'utf8'), '');
}
await writeFile(join(out, 'THIRD_PARTY_NOTICES.md'), licenses.join('\n'), 'utf8');
const manifest = { schema_version: 1, version: packageJson.version, build_tool: { name: 'esbuild', version: esbuildVersion }, target: 'node22.19', entries, sources: Object.fromEntries(Object.entries(sources).sort()), dependencies };
await writeFile(join(out, 'BUILD.json'), JSON.stringify(manifest, null, 2) + '\n', 'utf8');
console.log(JSON.stringify({ ok: true, version: manifest.version, entries, dependencies: dependencies.length }));
