import { pathToFileURL } from 'node:url';
import { selectRuntime } from './runtime-paths.mjs';

try {
  const runtime = await selectRuntime();
  const { serveUniversal } = await import(pathToFileURL(runtime.entry).href);
  await serveUniversal();
} catch (error) {
  console.error(`source-intake startup: ${error.message}`);
  process.exitCode = 1;
}
