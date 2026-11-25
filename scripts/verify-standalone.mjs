import { verifyStandalone } from '../plugin/runtime-paths.mjs';

const results = await Promise.all(['server', 'doctor'].map(kind => verifyStandalone({ kind })));
console.log(JSON.stringify({ ok: results.every(result => result.ok), results }, null, 2));
if (results.some(result => !result.ok)) process.exitCode = 1;
