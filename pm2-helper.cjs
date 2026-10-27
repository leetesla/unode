#!/usr/bin/env node
'use strict';

// Run with the NEW Node binary, as the existing PM2 service user.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const cp = require('node:child_process');

const HELP = `Usage: node pm2-helper.cjs --pm2-dir /absolute/path/to/node_modules/pm2 [options]

The Node executing this file is the target runtime. Install PM2 there first.
Default: inspect only. No install, restart, save, or startup configuration changes.

  --apply                 Save, back up, update PM2, and verify
  --allow-kill            Allow kill/resurrect ONLY if the daemon still uses old Node
  --interactive-kill      Ask before the conditional kill/resurrect fallback
  --backup-root PATH      Backup parent directory (used by unode)
  --pm2-home PATH          Existing PM2_HOME (default: environment or ~/.pm2)
  --timeout SECONDS        Per-command timeout (default: 120)
  --settle SECONDS         Verification window (default: 30; minimum 3)
  --health-url URL         Optional HTTP(S) health check after process verification
  --help                  Show help

Exit: 0 verified; 1 failed; 2 inspection found a runtime mismatch.
Both update and kill/resurrect may interrupt ALL apps in this PM2_HOME.
Startup commands are generated for manual execution, never run with sudo here.
`;

function die(message) { throw new Error(message); }
function readJson(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function real(file) { return fs.realpathSync(file); }
function quote(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

// Separate bounded worker: PM2 connections cannot keep the main script alive.
// getReport is an internal PM2 RPC. Fail closed if this PM2 version lacks it.
async function worker(dir, home) {
  process.env.PM2_HOME = home;
  const pm2 = require(dir);
  const call = (obj, method, ...args) => new Promise((resolve, reject) => {
    obj[method](...args, (err, result) => err ? reject(err) : resolve(result));
  });
  try {
    // Connect directly: inspection must never start an absent PM2 daemon.
    await call(pm2.Client, 'launchRPC');
    const report = await call(pm2.Client, 'executeRemote', 'getReport', {});
    const apps = await call(pm2, 'list');
    pm2.disconnect();
    process.stdout.write('\nPM2_NODE_SNAPSHOT:' + JSON.stringify({ report, apps }) + '\n');
  } finally { pm2.disconnect(); }
}

function options(args) {
  const opt = { apply: false, allowKill: false, home: process.env.PM2_HOME || path.join(os.homedir(), '.pm2'), timeout: 120, settle: 30 };
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--help') return { help: true };
    if (arg === '--apply') { opt.apply = true; continue; }
    if (arg === '--allow-kill') { opt.allowKill = true; continue; }
    if (arg === '--interactive-kill') { opt.interactiveKill = true; continue; }
    const fields = { '--pm2-dir': 'dir', '--pm2-home': 'home', '--timeout': 'timeout', '--settle': 'settle', '--health-url': 'health', '--backup-root': 'backupRoot' };
    if (!fields[arg] || !args[i + 1] || args[i + 1].startsWith('--')) die('Unknown option or missing value: ' + arg);
    opt[fields[arg]] = args[++i];
  }
  for (const field of ['timeout', 'settle']) {
    opt[field] = Number(opt[field]);
    if (!Number.isFinite(opt[field]) || opt[field] < 3 || opt[field] > 3600) die(field + ' must be 3..3600 seconds');
  }
  if (!opt.dir || !path.isAbsolute(opt.dir)) die('--pm2-dir must be an absolute PM2 package directory');
  if (!path.isAbsolute(opt.home)) die('--pm2-home must be absolute');
  if (opt.backupRoot && !path.isAbsolute(opt.backupRoot)) die('--backup-root must be absolute');
  if (opt.allowKill && !opt.apply) die('--allow-kill requires --apply');
  if (opt.health && !['http:', 'https:'].includes(new URL(opt.health).protocol)) die('Health URL must be HTTP(S)');
  return opt;
}

function checkDaemon(home) {
  const pidFile = path.join(home, 'pm2.pid');
  const stat = fs.statSync(pidFile);
  if (stat.uid !== process.getuid()) die('PM2 PID file belongs to another user; run as the original service user');
  const pid = Number(fs.readFileSync(pidFile, 'utf8').trim());
  if (!Number.isInteger(pid) || pid <= 1) die('Invalid PM2 PID');
  process.kill(pid, 0);
  // Never silently create a new daemon for a wrong/empty PM2_HOME.
  if (!fs.existsSync(path.join(home, 'rpc.sock'))) die('No PM2 RPC socket in this PM2_HOME');
  return pid;
}

function compact(s) {
  return {
    daemon: { node: s.report.node_version, argv0: s.report.argv0, pm2: s.report.pm2_version },
    apps: s.apps.map(a => ({ name: a.name, namespace: a.pm2_env.namespace, id: a.pm_id,
      pid: a.pid, mode: a.pm2_env.exec_mode, status: a.pm2_env.status,
      node: a.pm2_env.node_version, interpreter: a.pm2_env.exec_interpreter }))
  };
}
function appKey(app) {
  const e = app.pm2_env;
  const instanceVar = e.instance_var || 'NODE_APP_INSTANCE';
  const instance = e[instanceVar] ?? e.env?.[instanceVar];
  return JSON.stringify([e.namespace || 'default', e.name, e.pm_exec_path, e.pm_cwd, e.exec_mode,
    instance == null ? null : String(instance)]);
}
function mismatches(before, after, target) {
  const errors = [];
  const groups = new Map();
  for (const a of after.apps) {
    const key = appKey(a);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(a);
  }
  const oldGroups = new Map();
  for (const a of before.apps) {
    const key = appKey(a);
    if (!oldGroups.has(key)) oldGroups.set(key, []);
    oldGroups.get(key).push(a);
  }
  for (const [key, oldApps] of oldGroups) {
    const now = groups.get(key) || [];
    if (now.length !== oldApps.length) errors.push(`${oldApps[0].name}: instance count ${oldApps.length} -> ${now.length}`);
    const expectedOnline = oldApps.filter(a => a.pm2_env.status === 'online').length;
    const online = now.filter(a => a.pm2_env.status === 'online' && a.pid > 0);
    if (online.length !== expectedOnline) errors.push(`${oldApps[0].name}: online count ${expectedOnline} -> ${online.length}`);
    const expectedStopped = oldApps.filter(a => a.pm2_env.status === 'stopped').length;
    const stopped = now.filter(a => a.pm2_env.status === 'stopped' && !(a.pid > 0));
    if (stopped.length !== expectedStopped) errors.push(`${oldApps[0].name}: stopped count ${expectedStopped} -> ${stopped.length}`);
    for (const a of online) {
      if (a.pm2_env.exec_mode === 'cluster_mode' && String(a.pm2_env.node_version).replace(/^v/, '') !== target) {
        errors.push(`${a.name}/${a.pm_id}: cluster Node is ${a.pm2_env.node_version}, expected ${target}`);
      }
    }
  }
  for (const key of groups.keys()) if (!oldGroups.has(key)) errors.push('Unexpected application appeared during update');
  return errors;
}

async function main() {
  const opt = options(process.argv.slice(2));
  if (opt.help) { console.log(HELP); return; }
  if (process.platform === 'win32') die('This script supports Linux/macOS, not Windows');
  for (const key of ['PM2_RPC_PORT', 'PM2_PUB_PORT', 'PM2_PID_FILE_PATH']) {
    if (process.env[key]) die('Custom ' + key + ' is not supported by this script');
  }
  opt.dir = real(opt.dir); opt.home = real(opt.home);
  const pkg = readJson(path.join(opt.dir, 'package.json'));
  if (pkg.name !== 'pm2') die('--pm2-dir is not a PM2 package');
  const entry = path.join(opt.dir, 'bin', 'pm2');
  if (!fs.existsSync(entry)) die('Missing PM2 JavaScript CLI entry');
  const target = process.versions.node;
  const node = real(process.execPath);
  const env = { ...process.env, PM2_HOME: opt.home, NO_COLOR: '1', FORCE_COLOR: '0',
    PATH: path.dirname(node) + path.delimiter + (process.env.PATH || ''),
    _: node }; // PM2's report reads $_ rather than process.execPath.
  const user = os.userInfo();
  checkDaemon(opt.home);
  let backup;
  const lock = path.join(opt.home, '.node-change.lock');
  let locked = false;
  const execute = (file, args, name, timeout = opt.timeout * 1000) => {
    const result = cp.spawnSync(file, args, { env, encoding: 'utf8', timeout, killSignal: 'SIGKILL', maxBuffer: 64 * 1024 * 1024 });
    if (backup) fs.writeFileSync(path.join(backup, name + '.log'), (result.stdout || '') + '\n' + (result.stderr || ''), { mode: 0o600 });
    if (result.error || result.status !== 0) die(name + ' failed' + (result.error ? ': ' + result.error.message : ' (exit ' + result.status + ')') + (backup ? '; see private backup logs' : ''));
    return result.stdout;
  };
  const command = (...args) => execute(node, [entry, ...args], args[0]);
  const snapshot = () => {
    checkDaemon(opt.home);
    const output = execute(node, [__filename, '--internal-snapshot', opt.dir, opt.home], 'snapshot', Math.min(opt.timeout * 1000, 15000));
    const line = output.split('\n').find(s => s.startsWith('PM2_NODE_SNAPSHOT:'));
    if (!line) die('PM2 snapshot unavailable; refusing automatic kill');
    const s = JSON.parse(line.slice('PM2_NODE_SNAPSHOT:'.length));
    if (!s.report || !/^v?\d+\.\d+\.\d+/.test(s.report.node_version) || !Array.isArray(s.apps)) die('Unsupported PM2 snapshot format');
    if (Number(s.report.uid) !== process.getuid()) die('Daemon user mismatch');
    return s;
  };
  const daemonMatches = s => {
    if (s.report.node_version.replace(/^v/, '') !== target) return false;
    if (process.platform === 'linux') {
      const pid = checkDaemon(opt.home);
      try { return fs.statSync(`/proc/${pid}/exe`).ino === fs.statSync(node).ino && fs.statSync(`/proc/${pid}/exe`).dev === fs.statSync(node).dev; }
      catch { die('Cannot verify Linux daemon executable; refusing automatic kill'); }
    }
    // macOS: getReport node_path is environment-derived. Version only is reliable here.
    return true;
  };
  try {
    fs.mkdirSync(lock, { mode: 0o700 }); locked = true;
    fs.writeFileSync(path.join(lock, 'owner.json'), JSON.stringify({ pid: process.pid, user: user.username }), { mode: 0o600 });
    const before = snapshot();
    console.log(`Target Node: ${target} (${node}); PM2 package: ${pkg.version}; user: ${user.username}`);
    console.log('PM2_HOME: ' + opt.home);
    console.log(JSON.stringify(compact(before), null, 2));
    if (!before.apps.length) die('No applications found; check the user and PM2_HOME');
    if (before.apps.some(a => !['online', 'stopped'].includes(a.pm2_env.status))) die('Some apps are errored/launching/stopping; resolve their state before migration');
    if (before.apps.some(a => a.pm2_env.status === 'stopped' && a.pm2_env.pmx_module)) die('Stopped PM2 plugins require manual migration; native update automatically loads modules');
    // PM2 saves each row without `instances` and God.prepare restores status=stopped
    // without spawning it. Preserve that dump and verify stopped rows separately.
    if (before.apps.some(a => a.pm2_env.pm_id === undefined && a.pm_id === undefined)) die('Application identifiers missing');
    if (!opt.apply) {
      const errors = mismatches(before, before, target);
      const match = daemonMatches(before);
      console.log(match ? 'Daemon already uses target Node.' : 'Daemon needs migration.');
      for (const error of errors) console.log(error);
      console.log('Inspection only. Use --apply in a maintenance window; add --allow-kill to permit the conditional fallback.');
      process.exitCode = match && !errors.length ? 0 : 2;
      return;
    }
    const root = opt.backupRoot || path.join(opt.home, 'node-change-backups');
    fs.mkdirSync(root, { recursive: true, mode: 0o700 });
    fs.chmodSync(root, 0o700);
    backup = fs.mkdtempSync(path.join(root, new Date().toISOString().replace(/[:.]/g, '-') + '-'));
    fs.chmodSync(backup, 0o700);
    const write = (name, data) => fs.writeFileSync(path.join(backup, name), data, { mode: 0o600 });
    write('before.json', JSON.stringify(before, null, 2));
    for (const file of ['dump.pm2', 'dump.pm2.bak']) {
      if (fs.existsSync(path.join(opt.home, file))) write(file + '.original', fs.readFileSync(path.join(opt.home, file)));
    }
    console.log('Private backup: ' + backup);
    command('save');
    write('dump.pm2.pre-update', fs.readFileSync(path.join(opt.home, 'dump.pm2')));
    // Store explicit recovery/startup commands. Never eval PM2's printed sudo commands.
    const prefix = `env PM2_HOME=${quote(opt.home)} PATH=${quote(env.PATH)} ${quote(node)} ${quote(entry)}`;
    write('recovery.txt', `Run as ${user.username}. This restarts all managed apps.\n` +
      `If the active dump is unusable, first restore the saved dump:\ncp ${quote(path.join(backup, 'dump.pm2.pre-update'))} ${quote(path.join(opt.home, 'dump.pm2'))}\n` +
      `${prefix} resurrect\n\nThis recovers the process list, not an automatic Node rollback.\n`);
    write('startup-commands.txt', `Run as ${user.username}, after application checks pass.\n` +
      `These may need sudo commands printed by PM2. Execute those separately after review.\n` +
      `For custom service names/init systems, adapt these commands to the original service.\n\n` +
      `${prefix} unstartup\n${prefix} startup -u ${quote(user.username)} --hp ${quote(user.homedir)}\n${prefix} save\n`);
    if (process.platform === 'linux') {
      const unit = `pm2-${user.username}.service`;
      const result = cp.spawnSync('systemctl', ['cat', unit], { encoding: 'utf8', timeout: 5000 });
      if (result.status === 0) write('systemd-service.before.txt', result.stdout);
      console.log('Default systemd service inspected: ' + unit + ' (custom units require manual review).');
    }
    console.log('Running pm2 update; online apps may restart; stopped apps must remain stopped.');
    command('update');
    let after = snapshot();
    if (!daemonMatches(after)) {
      if (!opt.allowKill && opt.interactiveKill && process.stdin.isTTY) {
        const readline = require('node:readline/promises');
        const input = readline.createInterface({ input: process.stdin, output: process.stdout });
        try {
          const answer = await input.question('PM2 后台仍未切换到目标 Node。kill/resurrect 会停止所有受管应用，是否继续？[Y/n] ');
          opt.allowKill = ['', 'y', 'yes'].includes(answer.trim().toLowerCase());
        } finally { input.close(); }
      }
      if (!opt.allowKill) die('Daemon still uses a different Node. No kill performed. Review backup, then rerun with --apply --allow-kill if appropriate');
      // Use the known-good pre-update dump, not a possibly damaged/partial update list.
      const currentErrors = mismatches(before, after, target).filter(e => !e.includes('cluster Node is'));
      if (currentErrors.length) die('Process list/state changed; refusing kill: ' + currentErrors.join('; '));
      console.log('Daemon runtime mismatch confirmed: performing the permitted kill/resurrect fallback.');
      command('kill');
      fs.copyFileSync(path.join(backup, 'dump.pm2.pre-update'), path.join(opt.home, 'dump.pm2'));
      fs.chmodSync(path.join(opt.home, 'dump.pm2'), 0o600);
      command('resurrect');
      after = snapshot();
      if (!daemonMatches(after)) die('Daemon still does not use target Node after fallback; inspect PM2 configuration');
    } else console.log('Daemon uses target Node; pm2 kill is unnecessary.');

    const deadline = Date.now() + opt.settle * 2000;
    let previousRestarts = null, stableSince = 0, errors = [];
    while (Date.now() <= deadline) {
      after = snapshot();
      errors = mismatches(before, after, target);
      const restartState = JSON.stringify(after.apps.map(a => [a.pm_id, a.pid, a.pm2_env.restart_time]).sort());
      if (!errors.length && restartState === previousRestarts) {
        if (Date.now() - stableSince >= opt.settle * 1000) break;
      } else { stableSince = Date.now(); }
      previousRestarts = restartState;
      await sleep(1000);
    }
    if (errors.length || Date.now() - stableSince < opt.settle * 1000) die('Apps failed to stabilize: ' + (errors.join('; ') || 'processes keep restarting'));
    if (!daemonMatches(after)) die('Daemon runtime changed during verification');
    if (opt.health) {
      const response = await fetch(opt.health, { signal: AbortSignal.timeout(10000) });
      if (!response.ok) die('Health check returned HTTP ' + response.status);
      await response.body?.cancel();
      console.log('HTTP health check passed.');
    }
    command('save');
    write('after.json', JSON.stringify(after, null, 2));
    console.log(JSON.stringify(compact(after), null, 2));
    console.log('Runtime verification passed. Fork apps keep their configured runtime; their Node versions are displayed above.');
    console.log('Startup configuration has NOT been changed. If startup is configured, finish using: ' + path.join(backup, 'startup-commands.txt'));
    console.log('Backups/logs contain environment secrets; keep this directory private.');
  } finally {
    if (locked) fs.rmSync(lock, { recursive: true, force: true });
  }
}

if (process.argv[2] === '--internal-snapshot') {
  worker(process.argv[3], process.argv[4]).catch(err => { console.error(err.message); process.exitCode = 1; });
} else if (require.main === module) {
  main().catch(err => { console.error('ERROR: ' + err.message); process.exitCode = 1; });
}
module.exports = { options, compact, mismatches };
