const { test } = require('node:test');
const assert = require('node:assert/strict');
const { options, mismatches } = require('../pm2-helper.cjs');
const app = (mode = 'cluster_mode', version = '22.20.0', id = 0) => ({
  name: 'api', pm_id: id, pid: 123 + id,
  pm2_env: { name: 'api', namespace: 'default', pm_exec_path: '/srv/api.js', pm_cwd: '/srv', exec_mode: mode, status: 'online', node_version: version }
});
const snap = apps => ({ apps });
test('inspection is default and kill is opt-in', () => {
  const o = options(['--pm2-dir', '/srv/pm2']);
  assert.equal(o.apply, false); assert.equal(o.allowKill, false);
});
test('kill flag without apply is rejected', () => assert.throws(() => options(['--pm2-dir', '/srv/pm2', '--allow-kill'])));
test('invalid and missing options are rejected', () => {
  for (const args of [[], ['--pm2-dir', 'relative'], ['--pm2-dir'], ['--pm2-dir', '/p', '--settle', 'NaN'], ['--pm2-dir', '/p', '--settle', '0']]) assert.throws(() => options(args));
});
test('matching clusters pass', () => assert.deepEqual(mismatches(snap([app(), app(undefined, undefined, 1)]), snap([app(), app(undefined, undefined, 1)]), '22.20.0'), []));
test('stale cluster runtime fails', () => assert.match(mismatches(snap([app()]), snap([app('cluster_mode', '20.20.0')]), '22.20.0').join(), /cluster Node/));
test('pinned fork runtime is accepted', () => assert.deepEqual(mismatches(snap([app('fork_mode', '20.20.0')]), snap([app('fork_mode', '20.20.0')]), '22.20.0'), []));
test('missing worker fails', () => assert.match(mismatches(snap([app()]), snap([]), '22.20.0').join(), /count/));
test('offline worker fails', () => {
  const a = app(); a.pm2_env.status = 'errored'; a.pid = 0;
  assert.match(mismatches(snap([app()]), snap([a]), '22.20.0').join(), /online count/);
});
test('new unexpected app fails', () => {
  const a = app(); a.pm2_env.name = 'extra';
  assert.match(mismatches(snap([app()]), snap([app(), a]), '22.20.0').join(), /Unexpected/);
});
test('health URL protocols are restricted', () => assert.throws(() => options(['--pm2-dir', '/srv/pm2', '--health-url', 'file:///tmp/a'])));
test('stopped worker keeps stopped state and does not require a new runtime', () => {
  const a = app('cluster_mode', '20.20.0'); a.pm2_env.status = 'stopped'; a.pid = 0;
  assert.deepEqual(mismatches(snap([a]), snap([a]), '22.20.0'), []);
});
test('stopped worker that starts is rejected', () => {
  const a = app(); a.pm2_env.status = 'stopped'; a.pid = 0;
  assert.match(mismatches(snap([a]), snap([app()]), '22.20.0').join(), /stopped count/);
});
test('errored worker is not accepted as stopped', () => {
  const a = app(), b = app(); a.pm2_env.status = 'stopped'; a.pid = 0;
  b.pm2_env.status = 'errored'; b.pid = 0;
  assert.match(mismatches(snap([a]), snap([b]), '22.20.0').join(), /stopped count/);
});
