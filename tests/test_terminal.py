"""Real terminal tests. No global installations or live service changes."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pty
import pwd
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def terminal_run(command, env, answers, timeout=60):
    master, slave = pty.openpty()
    child = subprocess.Popen([str(v) for v in command], stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
    os.close(slave)
    output = b""
    index = 0
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                if not data:
                    break
                output += data
            if index < len(answers) and answers[index][0].encode() in output:
                os.write(master, answers[index][1])
                index += 1
            if child.poll() is not None:
                # Read remaining output in the next iteration, if any.
                if not select.select([master], [], [], 0.1)[0]:
                    break
        else:
            child.kill()
            raise AssertionError("terminal timeout:\n" + output.decode(errors="replace"))
        child.wait(timeout=5)
        if index != len(answers):
            raise AssertionError("not all prompts reached:\n" + output.decode(errors="replace"))
        return child.returncode, output.decode(errors="replace")
    finally:
        os.close(master)
        if child.poll() is None:
            child.kill()
            child.wait()


class TerminalTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node needed for isolated helper prompt")
    def test_helper_kill_prompt_enter_defaults_yes(self):
        with tempfile.TemporaryDirectory(prefix="unode-kill-prompt-") as temp:
            root = Path(temp)
            package = root / "package"
            home = root / "pm2"
            (package / "bin").mkdir(parents=True)
            home.mkdir()
            daemon = subprocess.Popen([shutil.which("node"), "-e", "setInterval(()=>{},1000)"])
            self.addCleanup(daemon.wait, timeout=5)
            self.addCleanup(daemon.terminate)
            (home / "pm2.pid").write_text(str(daemon.pid))
            (home / "rpc.sock").touch()
            (home / "state.json").write_text(json.dumps({"node": "20.0.0", "events": []}))
            (package / "package.json").write_text(json.dumps({"name": "pm2", "version": "0.0.0", "main": "index.cjs"}))
            (package / "index.cjs").write_text("""
const fs = require('node:fs');
const file = process.env.PM2_HOME + '/state.json';
module.exports = {
  disconnect() {},
  Client: {
    launchRPC(cb) { cb(); },
    executeRemote(name, arg, cb) { cb(null, {node_version:JSON.parse(fs.readFileSync(file)).node,uid:process.getuid()}); }
  },
  list(cb) { const s=JSON.parse(fs.readFileSync(file)); cb(null,[{name:'api',pm_id:0,pid:123,
    pm2_env:{name:'api',namespace:'default',pm_exec_path:'/api.js',pm_cwd:'/',exec_mode:'cluster_mode',node_version:s.node,status:'online',restart_time:0}}]); }
};
""")
            (package / "bin/pm2").write_text("""
const fs=require('node:fs'); const file=process.env.PM2_HOME+'/state.json';
const s=JSON.parse(fs.readFileSync(file)); const action=process.argv[2];
s.events.push(action);
if(action==='save') fs.writeFileSync(process.env.PM2_HOME+'/dump.pm2','[{}]');
if(action==='resurrect') s.node=process.versions.node;
fs.writeFileSync(file,JSON.stringify(s));
""")
            env = dict(os.environ)
            for key in ("PM2_RPC_PORT", "PM2_PUB_PORT", "PM2_PID_FILE_PATH"):
                env.pop(key, None)
            code, output = terminal_run([shutil.which("node"), ROOT / "pm2-helper.cjs", "--pm2-dir", package,
                                         "--pm2-home", home, "--apply", "--interactive-kill", "--settle", "3"],
                                        env, [("是否继续？", b"\n")])
            self.assertEqual(code, 0, output)
            self.assertIn("是否继续？[Y/n]", output)
            self.assertEqual(json.loads((home / "state.json").read_text())["events"], ["save", "update", "kill", "resurrect", "save"])

    def test_enter_accepts_focused_item_without_space(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, "-c", """
import importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader('u', %r)
spec = importlib.util.spec_from_loader(loader.name, loader)
u = importlib.util.module_from_spec(spec); loader.exec_module(u)
print('SELECTED=' + str(u.radio('MANAGERS', ['nvm', 'fnm', 'n'])))
""" % str(ROOT / "unode")]
            code, output = terminal_run(command, dict(os.environ, UNODE_HOME=temp), [("MANAGERS", b"\x1b[B\r")])
            self.assertEqual(code, 0, output)
            self.assertIn("SELECTED=1", output)

    def test_custom_version_input_appears_after_enter(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, "-c", """
import importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader('u', %r)
spec = importlib.util.spec_from_loader(loader.name, loader)
u = importlib.util.module_from_spec(spec); loader.exec_module(u)
u.versions = lambda: [{'version':'v24.1.0','lts':'test'}]
print('VERSION=' + u.select_version(u.parser().parse_args([])))
""" % str(ROOT / "unode")]
            code, output = terminal_run(command, dict(os.environ, UNODE_HOME=temp),
                [("输入具体版本", b"\x1b[A\r"), ("输入 Node 版本", b"22\n")])
            self.assertEqual(code, 0, output)
            self.assertIn("VERSION=22", output)

    def test_radio_selects_one_item_with_arrows_space_and_enter(self):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, "-c", """
import importlib.machinery, importlib.util
loader = importlib.machinery.SourceFileLoader('u', %r)
spec = importlib.util.spec_from_loader(loader.name, loader)
u = importlib.util.module_from_spec(spec); loader.exec_module(u)
print('SELECTED=' + str(u.radio('MANAGERS', ['nvm', 'fnm', 'n'])))
""" % str(ROOT / "unode")]
            code, output = terminal_run(command, dict(os.environ, UNODE_HOME=temp, UNODE_CONFIG=str(Path(temp) / "config.json")), [("MANAGERS", b"\x1b[B \r")])
            self.assertEqual(code, 0, output)
            self.assertIn("◉ nvm", output)
            self.assertIn("◉ fnm", output)
            self.assertIn("SELECTED=1", output)
            self.assertIn("\x1b[?25h", output)

    @unittest.skipUnless(shutil.which("node"), "Node is needed for the real path-mode terminal test")
    def test_no_pm2_terminal_flow(self):
        node = subprocess.check_output(["node", "-p", "process.execPath"], text=True).strip()
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state"
            pm2_home = Path(temp) / "pm2-never-created"
            env = dict(os.environ, HOME=temp, UNODE_HOME=str(state), UNODE_CONFIG=str(Path(temp) / "config.json"), PM2_HOME=str(pm2_home), PM2_RPC_PORT="deliberately-not-used")
            env["TERM"] = "xterm-256color"
            env.pop("NO_COLOR", None)
            code, output = terminal_run([sys.executable, ROOT / "unode", "--m", "path", "--node-path", node], env,
                                        [("是否处理 PM2", b"n\n"), ("是否 restartup", b"n\n"), ("执行上述计划", b"y\n")])
            self.assertEqual(code, 0, output)
            self.assertIn("PM2 已跳过", output)
            self.assertIn("当前 Node：\x1b[1;36m", output)
            plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output)
            self.assertIn("┌  unode", plain)
            self.assertIn("◇  当前环境", plain)
            self.assertIn("└  ✓ Node 处理完成", plain)
            self.assertNotIn("─" * 48, plain)
            self.assertIn("选择目标 Node 版本", output)
            self.assertFalse(pm2_home.exists())
            data = json.loads((state / "last-run.json").read_text())
            self.assertFalse(data["plan"]["process_pm2"])

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("node"), "macOS and Node needed for launchd fixture")
    def test_startup_node_comparison_in_real_terminal(self):
        node = subprocess.check_output(["node", "-p", "process.execPath"], text=True).strip()
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                binary = root / "bin/node"
                binary.parent.mkdir()
                if mismatch:
                    info = json.dumps({"version": "18.0.0", "path": str(binary)})
                    binary.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(info) + "\n")
                    binary.chmod(0o755)
                else:
                    binary.symlink_to(node)
                pm2 = binary.with_name("pm2")
                pm2.write_text("#!/usr/bin/env node\n")
                file = root / "Library/LaunchAgents" / ("pm2." + pwd.getpwuid(os.getuid()).pw_name + ".plist")
                file.parent.mkdir(parents=True)
                file.write_bytes(plistlib.dumps({"Label": "com.PM2", "EnvironmentVariables": {"PATH": str(binary.parent)},
                                                  "ProgramArguments": ["/bin/sh", "-c", str(pm2) + " resurrect"]}))
                env = dict(os.environ, HOME=temp, UNODE_HOME=str(root / "state"), UNODE_CONFIG=str(root / "config.json"), TERM="xterm")
                env.pop("NO_COLOR", None)
                code, output = terminal_run([sys.executable, ROOT / "unode", "--m", "path", "--node-path", node], env,
                                            [("是否处理 PM2", b"n\n"), ("是否 restartup", b"n\n"), ("执行上述计划", b"y\n")])
                self.assertEqual(code, 2 if mismatch else 0, output)
                self.assertIn("\x1b[1;31m" if mismatch else "\x1b[1;32m", output)
                self.assertIn("启动配置 Node", output)
                record = json.loads((root / "state/last-run.json").read_text())
                self.assertEqual(record["startup_node"]["status"], "mismatch" if mismatch else "match")


class RealPM2Tests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("UNODE_TEST_PM2_DIR") and os.environ.get("UNODE_TEST_OLD_NODE") and shutil.which("node"),
                         "opt in with an isolated PM2 package and old Node path")
    def test_real_cluster_migration(self):
        package = Path(os.environ["UNODE_TEST_PM2_DIR"]).resolve()
        old = Path(os.environ["UNODE_TEST_OLD_NODE"])
        target = Path(subprocess.check_output(["node", "-p", "process.execPath"], text=True).strip())
        with tempfile.TemporaryDirectory(prefix="unode-real-") as temp:
            root = Path(temp)
            home = root / "pm2"
            global_root = root / "npm-prefix/lib/node_modules"
            global_root.mkdir(parents=True)
            (global_root / "pm2").symlink_to(package, target_is_directory=True)
            app = root / "app.cjs"
            starts = root / "starts.jsonl"
            app.write_text("const fs=require('node:fs'); fs.appendFileSync(" + json.dumps(str(starts)) +
                           ",JSON.stringify({role:process.env.UNODE_TEST_ROLE,instance:process.env.NODE_APP_INSTANCE,pid:process.pid})+'\\n'); setInterval(() => {}, 1000);\n")
            ecosystem = root / "apps.json"
            ecosystem.write_text(json.dumps({"apps": [
                {"name": "cluster", "script": str(app), "instances": 2, "exec_mode": "cluster", "env": {"UNODE_TEST_ROLE": "cluster"}},
                {"name": "pinned", "script": str(app), "exec_mode": "fork", "interpreter": str(old), "env": {"UNODE_TEST_ROLE": "pinned"}},
                {"name": "stopped-task", "script": str(app), "exec_mode": "fork", "interpreter": str(old), "env": {"UNODE_TEST_ROLE": "stopped"}}]}))
            env = dict(os.environ, HOME=str(root), PM2_HOME=str(home), UNODE_HOME=str(root / "state"),
                       UNODE_CONFIG=str(root / "config.json"),
                       NPM_CONFIG_PREFIX=str(root / "npm-prefix"), PATH=str(old.parent) + os.pathsep + os.environ["PATH"],
                       _="/usr/local/bin/unode")
            env.pop("PM2_RPC_PORT", None)
            env.pop("PM2_PUB_PORT", None)
            entry = package / "bin/pm2"
            def pm2(node, *args):
                return subprocess.run([str(node), str(entry), *map(str, args)], env=env, capture_output=True, text=True, timeout=30)
            def get_snapshot(node):
                report = subprocess.check_output([str(node), str(ROOT / "pm2-helper.cjs"), "--internal-snapshot", str(package), str(home)], env=env, text=True, timeout=20)
                marker = "PM2_NODE_SNAPSHOT:"
                return json.loads(next(line[len(marker):] for line in report.splitlines() if line.startswith(marker)))
            def events():
                return [json.loads(line) for line in starts.read_text().splitlines()] if starts.exists() else []
            try:
                started = pm2(old, "start", ecosystem)
                self.assertEqual(started.returncode, 0, started.stderr)
                until = time.monotonic() + 5
                while len(events()) < 4 and time.monotonic() < until:
                    time.sleep(0.05)
                self.assertEqual(len(events()), 4)
                first = get_snapshot(old)
                worker = next(a for a in first["apps"] if a["name"] == "cluster")
                instance = str(worker["pm2_env"]["NODE_APP_INSTANCE"])
                self.assertEqual(pm2(old, "stop", worker["pm_id"]).returncode, 0)
                self.assertEqual(pm2(old, "stop", "stopped-task").returncode, 0)
                def stopped_starts():
                    return len([e for e in events() if e["role"] == "stopped" or (e["role"] == "cluster" and str(e["instance"]) == instance)])
                original_starts = stopped_starts()
                command = [sys.executable, ROOT / "unode", "--m", "path", "--node-path", target,
                           "--pm2-dir", package, "--settle", "3"]
                code, output = terminal_run(command, env, [("是否处理 PM2", b"y\n"), ("是否 restartup", b"n\n"), ("执行上述计划", b"y\n")])
                self.assertIn(code, (0, 2), output)
                self.assertIn("kill is unnecessary", output)
                self.assertIn("运行步骤已完成", output)
                data = get_snapshot(target)
                target_version = subprocess.check_output([str(target), "-p", "process.versions.node"], text=True).strip()
                old_version = subprocess.check_output([str(old), "-p", "process.versions.node"], text=True).strip()
                self.assertEqual(data["report"]["node_version"], target_version)
                self.assertEqual(data["report"]["node_path"], str(target))
                self.assertEqual(len(data["apps"]), 4)
                self.assertTrue(all(a["pm2_env"]["node_version"] == target_version for a in data["apps"] if a["pm2_env"]["exec_mode"] == "cluster_mode" and a["pm2_env"]["status"] == "online"))
                self.assertEqual(len([a for a in data["apps"] if a["pm2_env"]["status"] == "stopped"]), 2)
                self.assertEqual(stopped_starts(), original_starts, "stopped apps were briefly launched during update")
                self.assertEqual(next(a for a in data["apps"] if a["name"] == "pinned")["pm2_env"]["node_version"], old_version)
                record = json.loads((root / "state/last-run.json").read_text())
                self.assertTrue(record["plan"]["process_pm2"])
                backup = Path(record["backup"])
                self.assertTrue((backup / "dump.pm2.pre-node").exists())
                self.assertEqual((backup / "pm2-before.json").stat().st_mode & 0o777, 0o600)
                self.assertFalse((home / "node-change-backups").exists())
                # Exercise the native kill/resurrect fallback on this isolated
                # mixed table too. Stopped launch markers must not increase.
                self.assertEqual(pm2(target, "kill").returncode, 0)
                self.assertEqual(pm2(target, "resurrect").returncode, 0)
                restored = get_snapshot(target)
                self.assertEqual(len([a for a in restored["apps"] if a["pm2_env"]["status"] == "stopped"]), 2)
                time.sleep(0.3)
                self.assertEqual(stopped_starts(), original_starts)
                # An entirely stopped process table is valid as well. Updating
                # its daemon must not execute a single application entry point.
                self.assertEqual(pm2(target, "stop", "all").returncode, 0)
                all_starts = len(events())
                result = subprocess.run([str(target), str(ROOT / "pm2-helper.cjs"), "--pm2-dir", str(package),
                    "--pm2-home", str(home), "--apply", "--settle", "3", "--backup-root", str(root / "all-stopped")],
                    env=env, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                final = get_snapshot(target)
                self.assertEqual(final["report"]["node_path"], str(target))
                self.assertTrue(all(a["pm2_env"]["status"] == "stopped" for a in final["apps"]))
                self.assertEqual(len(events()), all_starts)
            finally:
                pm2(target, "kill")


if __name__ == "__main__":
    unittest.main()
