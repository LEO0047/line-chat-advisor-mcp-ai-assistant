import { execFileSync } from 'node:child_process';
import assert from 'node:assert/strict';
const [pack] = JSON.parse(execFileSync('npm', ['pack', '--dry-run', '--json', '--ignore-scripts'], { encoding: 'utf8' }));
const files = pack.files.map(f => f.path);
for (const path of files) {
  assert(!/(^|\/)(data|runtime|vendor|node_modules|\.venv|contacts|__pycache__)(\/|$)/.test(path), `Private/runtime directory: ${path}`);
  assert(!/(\.env|\.npmrc|\.(db|edb|sqlite3?|log|png|jpg|pem|key|pyc)$)/i.test(path), `Unexpected sensitive artifact: ${path}`);
}
for (const required of ['bin/line-context-relay.mjs','src/line-readonly-mcp/bridge.py','npm-shrinkwrap.json','requirements.lock','INSTALL.md','SECURITY.md','MAINTAINERS.md','scripts/bootstrap']) assert(files.includes(required), `Missing ${required}`);
console.log(`Verified ${files.length} package files; runtime/private paths excluded.`);
