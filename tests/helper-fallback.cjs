const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const cp = require('node:child_process');
const script = path.resolve(__dirname, '../pm2-helper.cjs');

function scenario(mode, args, stopped = false) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-pm2-fake-'));
  const pkg = path.join(root, 'pm2');
  const home = path.join(root, 'home');
  fs.mkdirSync(path.join(pkg, 'bin'), { recursive: true }); fs.mkdirSync(home);
  fs.writeFileSync(path.join(home, 'pm2.pid'), String(process.pid));
  fs.writeFileSync(path.join(home, 'rpc.sock'), '');
  const state = { node: '20.0.0', appNode: '20.0.0', mode, events: [], stopped };
  fs.writeFileSync(path.join(home, 'state.json'), JSON.stringify(state));
  fs.writeFileSync(path.join(pkg, 'package.json'), JSON.stringify({ name: 'pm2', version: '0.0.0', main: 'index.cjs' }));
  fs.writeFileSync(path.join(pkg, 'index.cjs'), `
const fs = require('node:fs');
const path = require('node:path');
const file = path.join(process.env.PM2_HOME, 'state.json');
const state = () => JSON.parse(fs.readFileSync(file));
module.exports = {
  connect: cb => cb(), disconnect: () => {},
  Client: { launchRPC: cb => cb(), executeRemote: (name, arg, cb) => {
    const s = state();
    if (s.mode === 'bad-report' && s.events.includes('update')) return cb(new Error('RPC unavailable'));
    cb(null, { node_version: s.node, uid: process.getuid(), pm2_version: '0.0.0' });
  } },
  list: cb => { const s = state(); cb(null, [{ name: 'api', pm_id: 0, pid: s.stopped ? 0 : 123,
    pm2_env: { name: 'api', namespace: 'default', pm_exec_path: '/api.js', pm_cwd: '/', exec_mode: 'cluster_mode',
    node_version: s.appNode, status: s.stopped ? 'stopped' : 'online', restart_time: 0 } }]); }
};
`);
  fs.writeFileSync(path.join(pkg, 'bin/pm2'), `
const fs = require('node:fs'); const path = require('node:path');
const file = path.join(process.env.PM2_HOME, 'state.json');
const s = JSON.parse(fs.readFileSync(file)); const command = process.argv[2];
s.events.push(command);
s.nodePath = process.env._;
if (command === 'save') fs.writeFileSync(path.join(process.env.PM2_HOME, 'dump.pm2'), '[{}]');
if (command === 'update' && ['success', 'bad-app'].includes(s.mode)) { s.node = process.versions.node; if (s.mode !== 'bad-app') s.appNode = s.node; }
if (command === 'resurrect') { s.node = process.versions.node; s.appNode = s.node; }
fs.writeFileSync(file, JSON.stringify(s));
if (command === 'update' && s.mode === 'update-error') process.exit(1);
`);
  try {
    const r = cp.spawnSync(process.execPath, [script, '--pm2-dir', pkg, '--pm2-home', home, '--settle', '3', ...args], {
      env: { ...process.env, _: '/usr/local/bin/unode' }, encoding: 'utf8', timeout: 30000
    });
    return { ...r, state: JSON.parse(fs.readFileSync(path.join(home, 'state.json'))) };
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

test('update success never calls kill even when permitted', () => {
  const r = scenario('success', ['--apply', '--allow-kill']);
  assert.equal(r.status, 0, r.stderr); assert.deepEqual(r.state.events, ['save', 'update', 'save']);
  assert.equal(r.state.nodePath, fs.realpathSync(process.execPath));
});
test('daemon mismatch without allow-kill stops without kill', () => {
  const r = scenario('stale', ['--apply']);
  assert.equal(r.status, 1); assert.deepEqual(r.state.events, ['save', 'update']);
});
test('permitted daemon mismatch runs kill/resurrect then verifies', () => {
  const r = scenario('stale', ['--apply', '--allow-kill']);
  assert.equal(r.status, 0, r.stderr); assert.deepEqual(r.state.events, ['save', 'update', 'kill', 'resurrect', 'save']);
});
test('update failure does not trigger kill', () => {
  const r = scenario('update-error', ['--apply', '--allow-kill']);
  assert.equal(r.status, 1); assert.deepEqual(r.state.events, ['save', 'update']);
});
test('unreadable daemon report does not trigger kill', () => {
  const r = scenario('bad-report', ['--apply', '--allow-kill']);
  assert.equal(r.status, 1); assert.deepEqual(r.state.events, ['save', 'update']);
});
test('application-only mismatch does not trigger kill', () => {
  const r = scenario('bad-app', ['--apply', '--allow-kill']);
  assert.equal(r.status, 1); assert.deepEqual(r.state.events, ['save', 'update']);
});
test('stopped application is preserved through allowed fallback', () => {
  const r = scenario('stale', ['--apply', '--allow-kill'], true);
  assert.equal(r.status, 0, r.stderr); assert.deepEqual(r.state.events, ['save', 'update', 'kill', 'resurrect', 'save']);
  assert.equal(r.state.stopped, true);
});
test('failed HTTP health check does not mark migration successful', () => {
  const r = scenario('success', ['--apply', '--allow-kill', '--health-url', 'http://127.0.0.1:1/health']);
  assert.equal(r.status, 1); assert.deepEqual(r.state.events, ['save', 'update']);
});
