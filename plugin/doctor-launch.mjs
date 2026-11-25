import { pathToFileURL } from 'node:url';
import { selectRuntime } from './runtime-paths.mjs';

try {
  const runtime = await selectRuntime({ kind: 'doctor' });
  const { doctorCLI } = await import(pathToFileURL(runtime.entry).href);
  await doctorCLI();
} catch (error) {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exitCode = 1;
}
