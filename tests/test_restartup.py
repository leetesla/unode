"""Service lifecycle tests use temporary files; no real service is changed."""
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from unittest.mock import patch

from test_unode import u, application, snapshot


class RestartupTests(unittest.TestCase):
    def scenario(self, system="systemd", exists=True, fail=None, no_daemon=False, node_match=True,
                 once=False, disappears=False, never_registered=False, empty=False,
                 unmanaged=False, saved_dump=None, backup_dump=None, orphan_socket=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root = Path(temp)
            home = root / "custom-pm2"
            home.mkdir()
            file = root / ("pm2-custom.service" if system == "systemd" else "pm2.custom.plist")
            if exists:
                file.write_bytes(b"original startup definition\n")
            target = {"version": "22.23.4", "path": "/runtime/bin/node"}
            package = {"dir": "/runtime/lib/node_modules/pm2", "version": "6.0.13"}
            online, stopped = application(version=target["version"]), application("fork_mode", "20.0.0", "job")
            stopped["pid"], stopped["pm2_env"]["status"] = 0, "stopped"
            before = snapshot([] if empty or no_daemon else [online, stopped], target["version"])
            if saved_dump is not None:
                (home / "dump.pm2").write_bytes(saved_dump)
            if backup_dump is not None:
                (home / "dump.pm2.bak").write_bytes(backup_dump)
            if orphan_socket:
                (home / "rpc.sock").touch()
            backend = {"daemon": not no_daemon, "loaded": exists, "owned": exists and not unmanaged, "events": []}
            args = u.argparse.Namespace(pm2_home=home, pm2_dir=None, service="pm2-custom", settle=0, health_url=None)
            env = dict(u.node_env(target["path"]), PM2_HOME=str(home), HOME=temp, USER="fixture", LOGNAME="fixture")
            context = {"package": package, "env": env, "file": file, "exists": exists, "platform": system,
                       "service": "pm2-custom", "unit": "pm2-custom.service",
                       "jobs": ["gui/501/com.PM2"] if exists and system == "launchd" else []}
            state = u.State(root / "state", root / "missing-config")
            state.start({"action": "restartup"})

            def execute(command, **kwargs):
                command = [str(v) for v in command]
                if command[:1] == [target["path"]]:
                    verb = command[2]
                    backend["events"].append(verb)
                    if verb == "save":
                        (home / "dump.pm2").write_text(json.dumps([a["pm2_env"] for a in before["apps"]]))
                    elif verb == "resurrect":
                        backend["daemon"] = True
                    elif verb == "kill":
                        backend["daemon"] = False
                return subprocess.CompletedProcess(command, 0, "", "")

            def admin(command, env, log, check=True):
                command = [str(v) for v in command]
                verb = command[2] if command[0] == target["path"] else command[1]
                backend["events"].append(verb)
                if verb == fail:
                    raise u.UnodeError("fixture " + verb + " failure")
                if verb == "unstartup":
                    file.unlink()
                    if system == "systemd" and backend["owned"]:
                        backend["daemon"] = False
                elif verb == "bootout":
                    backend["loaded"], backend["daemon"] = False, False
                elif verb == "startup":
                    if system == "launchd":
                        file.write_bytes(u.plistlib.dumps({"Label": "com.PM2", "LaunchOnlyOnce": once,
                                                         "EnvironmentVariables": {"PM2_HOME": str(home)}}))
                        backend["loaded"], backend["daemon"] = not never_registered, True
                    else:
                        file.write_text("new startup definition\n")
                elif verb == "start":
                    if backend["daemon"]:
                        raise u.UnodeError("fixture systemd refuses a daemon outside its service")
                    backend["daemon"] = True
                    backend["owned"] = True
                elif verb == "stop":
                    backend["daemon"] = False
                return subprocess.CompletedProcess(command, 0, "", "")

            stack.enter_context(patch.object(u, "pm2_snapshot", side_effect=lambda *a: before if backend["daemon"] else None))
            stack.enter_context(patch.object(u, "daemon_pid", side_effect=lambda *a: 123 if backend["daemon"] else None))
            authorize = stack.enter_context(patch.object(u, "startup_authorize"))
            stack.enter_context(patch.object(u, "startup_admin", side_effect=admin))
            stack.enter_context(patch.object(u, "run", side_effect=execute))
            stack.enter_context(patch.object(u, "launchd_jobs", side_effect=lambda *a: ["gui/501/com.PM2"] if backend["loaded"] else []))
            stack.enter_context(patch.object(u, "launchagent_config", side_effect=lambda *a: (file, u.plistlib.loads(file.read_bytes()))))
            stack.enter_context(patch.object(u, "startup_check", return_value={"status": "config-passed", "details": "fixture service verified"}))
            stack.enter_context(patch.object(u, "startup_node_check", return_value={"status": "match" if node_match else "mismatch", "node": target}))
            original_wait = u.wait_startup_apps
            def wait(*arguments):
                result = original_wait(*arguments)
                if disappears:
                    backend["loaded"] = False
                return result
            stack.enter_context(patch.object(u, "wait_startup_apps", side_effect=wait))
            stack.enter_context(redirect_stdout(io.StringIO()))
            if fail == "authorization":
                authorize.side_effect = u.UnodeError("authorization rejected")
            try:
                result = u.perform_restartup(args, target, state, context)
                error = None
            except u.UnodeError as exc:
                result, error = None, str(exc)
            recovery = state.folder / "restartup-recovery.json"
            saved_original = state.folder / "startup-config.original"
            return {**backend, "result": result, "error": error,
                    "recovery": json.loads(recovery.read_text()) if recovery.exists() else None,
                    "backup": saved_original.read_bytes() if saved_original.exists() else None,
                    "dump": (home / "dump.pm2").read_bytes() if (home / "dump.pm2").exists() else None,
                    "config_exists": file.exists(), "authorization_called": authorize.called}

    def test_systemd_rebuild_restores_apps_via_service(self):
        result = self.scenario()
        self.assertIsNone(result["error"])
        self.assertEqual(result["events"], ["save", "unstartup", "startup", "644", "daemon-reload", "reset-failed", "start"])
        self.assertEqual(result["backup"], b"original startup definition\n")
        self.assertTrue(result["daemon"])
        self.assertEqual(result["result"]["status"], "config-passed")

    def test_first_configuration_skips_unstartup(self):
        result = self.scenario(exists=False)
        self.assertNotIn("unstartup", result["events"])
        self.assertTrue(result["config_exists"])
        self.assertIsNone(result["error"])

    def test_first_configuration_hands_terminal_daemon_to_systemd(self):
        result = self.scenario(exists=False)
        self.assertIsNone(result["error"])
        self.assertLess(result["events"].index("save"), result["events"].index("kill"))
        self.assertLess(result["events"].index("kill"), result["events"].index("startup"))
        self.assertTrue(result["owned"])

    def test_inactive_service_does_not_leave_terminal_daemon_running(self):
        result = self.scenario(unmanaged=True)
        self.assertIsNone(result["error"])
        self.assertLess(result["events"].index("unstartup"), result["events"].index("kill"))
        self.assertLess(result["events"].index("kill"), result["events"].index("start"))
        self.assertTrue(result["owned"])

    def test_launchd_unloads_actual_label(self):
        result = self.scenario(system="launchd")
        self.assertEqual(result["events"], ["save", "unstartup", "bootout", "startup", "644"])
        self.assertIsNone(result["error"])

    def test_launchd_oneshot_can_unregister_during_app_verification(self):
        result = self.scenario(system="launchd", once=True, disappears=True)
        self.assertIsNone(result["error"])
        self.assertTrue(result["daemon"])
        self.assertEqual(result["result"]["status"], "config-passed")
        self.assertEqual(result["result"]["launchd_status"], "oneshot-unregistered")
        self.assertTrue(result["result"]["registration_observed"])
        self.assertNotIn("服务加载及应用已验证", result["result"]["details"])

    def test_launchd_unobserved_oneshot_registration_remains_unverified(self):
        result = self.scenario(system="launchd", once=True, never_registered=True)
        self.assertIsNone(result["error"])
        self.assertTrue(result["daemon"])
        self.assertEqual(result["result"]["status"], "needs-review")
        self.assertFalse(result["result"]["registration_observed"])
        self.assertIn("无法确认启动执行结果", result["result"]["details"])

    def test_launchd_regular_service_disappearing_is_a_failure(self):
        result = self.scenario(system="launchd", disappears=True)
        self.assertIn("不再加载", result["error"])

    def test_unstartup_failure_does_not_run_startup(self):
        result = self.scenario(fail="unstartup")
        self.assertNotIn("startup", result["events"])
        self.assertTrue(result["daemon"])
        self.assertIn("unstartup failure", result["error"])

    def test_startup_failure_recovers_saved_apps(self):
        result = self.scenario(fail="startup")
        self.assertIn("resurrect", result["events"])
        self.assertTrue(result["daemon"])
        self.assertEqual(result["recovery"]["status"], "applications-available")
        self.assertIsNone(result["result"])
        self.assertIn("startup failure", result["error"])

    def test_authorization_failure_has_no_pm2_mutation(self):
        result = self.scenario(fail="authorization")
        self.assertEqual(result["events"], [])
        self.assertTrue(result["config_exists"])

    def test_absent_daemon_initializes_empty_service_without_terminal_resurrect(self):
        result = self.scenario(no_daemon=True, exists=False)
        self.assertIsNone(result["error"])
        self.assertTrue(result["owned"])
        self.assertNotIn("save", result["events"])
        self.assertNotIn("resurrect", result["events"])
        self.assertEqual(json.loads(result["dump"]), [])
        self.assertEqual(result["result"]["application_count"], 0)

    def test_existing_empty_daemon_can_be_rebuilt(self):
        result = self.scenario(empty=True, unmanaged=True, saved_dump=b"[]")
        self.assertIsNone(result["error"])
        self.assertIn("kill", result["events"])
        self.assertNotIn("save", result["events"])
        self.assertTrue(result["owned"])

    def test_empty_initialization_failure_does_not_create_unmanaged_daemon(self):
        result = self.scenario(no_daemon=True, exists=False, fail="start")
        self.assertIn("start failure", result["error"])
        self.assertFalse(result["daemon"])
        self.assertNotIn("resurrect", result["events"])
        self.assertEqual(result["recovery"]["status"], "no-applications")

    def test_absent_daemon_does_not_restore_or_overwrite_stale_dump(self):
        original = b'[{"name":"saved-app"}]'
        result = self.scenario(no_daemon=True, saved_dump=original)
        self.assertEqual(result["events"], [])
        self.assertFalse(result["authorization_called"])
        self.assertIn("未覆盖或自动恢复", result["error"])
        self.assertEqual(result["dump"], original)

    def test_empty_daemon_does_not_overwrite_saved_applications(self):
        result = self.scenario(empty=True, saved_dump=b'[{"name":"saved-app"}]')
        self.assertEqual(result["events"], [])
        self.assertFalse(result["authorization_called"])

    def test_initialization_protects_backup_and_corrupt_dumps(self):
        for original in (b'[{"name":"saved-app"}]', b'invalid json', b'{}'):
            with self.subTest(original=original):
                result = self.scenario(no_daemon=True, saved_dump=b'[]', backup_dump=original)
                self.assertEqual(result["events"], [])
                self.assertFalse(result["authorization_called"])
                self.assertIn("dump.pm2.bak", result["error"])

    def test_missing_pid_with_rpc_socket_requires_inspection(self):
        result = self.scenario(no_daemon=True, orphan_socket=True)
        self.assertEqual(result["events"], [])
        self.assertIn("PID 无法核实", result["error"])

    def test_node_mismatch_is_not_success(self):
        result = self.scenario(node_match=False)
        self.assertEqual(result["result"]["status"], "needs-review")


class RestartupEnvironmentTests(unittest.TestCase):
    def test_unloaded_disk_configuration_is_not_overwritten(self):
        args = u.argparse.Namespace(pm2_dir=None, pm2_home=Path("/srv/pm2"), service="custom")
        with patch.object(u.platform, "system", return_value="Linux"), patch.object(u.shutil, "which", return_value="/bin/systemctl"), \
                patch.object(u, "pm2_package", return_value={"dir": "/target/pm2"}), patch.object(u.Path, "exists", return_value=True), \
                patch.object(u, "run", return_value=subprocess.CompletedProcess([], 1, "LoadState=not-found\n", "")), self.assertRaises(u.UnodeError):
            u.restartup_context(args, {"version": "22.23.4", "path": "/target/node"})

    def test_upgrade_restartup_yes_uses_target_and_only_one_prompt(self):
        target = {"version": "22.23.4", "path": "/new/node"}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"UNODE_HOME": temp + "/state", "UNODE_CONFIG": temp + "/missing.json"}), \
                patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=target), \
                patch.object(u, "confirm", side_effect=[False, True, True]) as confirm, \
                patch.object(u, "perform_restartup", return_value={"status": "config-passed"}) as restartup, \
                patch.object(u, "startup_node_check", return_value={"status": "match", "node": target}), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.main(["--m", "path", "--node-path", target["path"], "--pm2-home", temp + "/pm2"]), 0)
            self.assertEqual(confirm.call_count, 3)
            self.assertEqual(sum("是否 restartup" in call.args[0] for call in confirm.call_args_list), 1)
            self.assertIn("是否 restartup", confirm.call_args_list[1].args[0])
            self.assertTrue(confirm.call_args_list[1].kwargs["default"])
            self.assertIn("执行上述计划", confirm.call_args_list[2].args[0])
            self.assertEqual(restartup.call_args.args[1], target)
            self.assertIn("Node 与 startup 处理完成", output.getvalue())
            record = json.loads((Path(temp) / "state/last-run.json").read_text())
            self.assertTrue(record["plan"]["restartup"])

    def test_restartup_choice_is_confirmed_before_any_upgrade(self):
        target = {"version": "22.23.4", "path": "/new/node"}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"UNODE_HOME": temp + "/state", "UNODE_CONFIG": temp + "/missing.json"}), \
                patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=target), \
                patch.object(u, "confirm", side_effect=[False, True, False]), \
                patch.object(u, "perform_restartup", side_effect=AssertionError("plan cancelled")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.main(["--m", "path", "--node-path", target["path"], "--pm2-home", temp + "/pm2"]), 0)
            self.assertFalse((Path(temp) / "state").exists())
            self.assertFalse((Path(temp) / "pm2").exists())
            self.assertIn("startup：使用目标 Node 重建", output.getvalue())

    def test_failed_startup_keeps_verified_node_as_comparison_reference(self):
        target = {"version": "22.23.4", "path": "/new/node"}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"UNODE_HOME": temp}):
            (Path(temp) / "last-run.json").write_text(json.dumps({"status": "failed", "target": target, "node_verified": True}))
            self.assertEqual(u.startup_reference(), (target, "最近安装目标"))

    def test_custom_systemd_dropin_is_not_overwritten(self):
        target = {"version": "22.23.4", "path": "/target/bin/node"}
        package = {"dir": "/target/lib/node_modules/pm2", "version": "6.0.13"}
        properties = "LoadState=loaded\nFragmentPath=/etc/systemd/system/custom.service\nDropInPaths=/etc/systemd/system/custom.service.d/override.conf\n"
        args = u.argparse.Namespace(pm2_dir=None, pm2_home=Path("/srv/pm2"), service="custom")
        with patch.object(u.platform, "system", return_value="Linux"), patch.object(u.shutil, "which", return_value="/bin/systemctl"), \
                patch.object(u, "pm2_package", return_value=package), \
                patch.object(u, "run", return_value=subprocess.CompletedProcess([], 0, properties, "")), self.assertRaises(u.UnodeError):
            u.restartup_context(args, target)

    def test_admin_uses_explicit_target_environment(self):
        env = {"PATH": "/target/bin:/usr/bin:/bin", "HOME": "/Users/app", "USER": "app", "LOGNAME": "app", "PM2_HOME": "/srv/custom"}
        with patch.object(u.os, "getuid", return_value=501), patch.object(u, "run") as run:
            u.startup_admin(["/target/bin/node", "/target/pm2", "startup"], env, None)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["sudo", "-n", "--"])
        self.assertIn("PATH=/target/bin:/usr/bin:/bin", command)
        self.assertIn("PM2_HOME=/srv/custom", command)
        self.assertEqual(command[-3:], ["/target/bin/node", "/target/pm2", "startup"])

    def test_nvm_environment_matches_target(self):
        with patch.dict(os.environ, {"NVM_BIN": "/old/bin", "NVM_INC": "/old/include"}):
            env = u.node_env("/home/app/.nvm/versions/node/v22.23.4/bin/node")
        self.assertEqual(env["NVM_BIN"], "/home/app/.nvm/versions/node/v22.23.4/bin")
        self.assertEqual(env["NVM_INC"], "/home/app/.nvm/versions/node/v22.23.4/include/node")

    def test_other_launchd_job_is_not_unloaded(self):
        output = subprocess.CompletedProcess([], 0, "path = /Users/another/pm2.plist\n", "")
        with patch.object(u, "run", return_value=output), self.assertRaises(u.UnodeError):
            u.launchd_jobs("com.PM2", Path("/Users/app/pm2.plist"))

    def test_launchd_query_failure_prevents_modifying_unknown_services(self):
        with patch.object(u, "run", return_value=subprocess.CompletedProcess([], 1, "", "Operation not permitted")), \
                self.assertRaisesRegex(u.UnodeError, "无法完整查询"):
            u.launchd_jobs("com.PM2", Path("/Users/app/pm2.plist"))

    def test_restartup_check_has_no_authorization_or_state_writes(self):
        target = {"version": "22.23.4", "path": "/target/bin/node"}
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"UNODE_HOME": temp + "/state"}), \
                patch.object(u, "node_info", return_value=target), \
                patch.object(u, "restartup_context", return_value={"file": Path(temp) / "pm2.plist"}), \
                patch.object(u, "startup_node_check", return_value={"status": "match", "node": target}), \
                patch.object(u, "startup_authorize", side_effect=AssertionError("read only")), redirect_stdout(io.StringIO()):
            self.assertEqual(u.main(["restartup", "--check"]), 0)
            self.assertFalse((Path(temp) / "state").exists())


class NvmPackageTests(unittest.TestCase):
    def test_native_nvm_parameter_is_optional(self):
        for choice in (False, True):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as temp:
                state = u.State(Path(temp) / "state", Path(temp) / "config.json")
                manager = u.Manager("nvm", state)
                target = {"version": "22.23.4", "path": "/target/node"}
                with patch.object(manager, "bootstrap_nvm"), patch.object(manager, "installed", return_value=target), patch.object(manager, "nvm") as command:
                    manager.install(target["version"], {"reinstall_from": "22.20.0"} if choice else None)
                expected = ["install", "22.23.4"] + (["--reinstall-packages-from=22.20.0"] if choice else [])
                command.assert_called_once_with(expected, timeout=1800)

    def test_nvm_prompt_defaults_yes_and_excludes_pnpm_globals(self):
        current = {"version": "22.20.0", "path": "/old/node"}
        target = {"version": "22.23.4", "path": "/new/node"}
        for choice in (False, True):
            with self.subTest(choice=choice), tempfile.TemporaryDirectory() as temp, \
                    patch.dict(os.environ, {"UNODE_HOME": temp + "/state", "UNODE_CONFIG": temp + "/missing.json"}), \
                    patch.object(u, "interactive", return_value=True), patch.object(u.Manager, "available", return_value=True), \
                    patch.object(u.Manager, "installed", side_effect=[None, current]), patch.object(u.Manager, "install", return_value=target) as install, \
                    patch.object(u, "node_info", side_effect=[current, target]), \
                    patch.object(u, "confirm", side_effect=[choice, False, False, False, True]) as confirm, \
                    patch.object(u, "startup_node_check", return_value={"status": "not-configured", "details": "fixture"}), redirect_stdout(io.StringIO()):
                self.assertEqual(u.main(["22.23.4", "--m", "nvm"]), 0)
                self.assertIn("不包含 pnpm add -g 安装的包", confirm.call_args_list[0].args[0])
                self.assertTrue(confirm.call_args_list[0].kwargs["default"])
                install.assert_called_once_with("22.23.4", {"reinstall_from": "22.20.0"} if choice else None)


class RealRestartupTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("UNODE_TEST_PM2_DIR") and shutil.which("node"), "opt in with isolated PM2 package")
    def test_real_pm2_recovery_preserves_stopped_apps(self):
        package = {"dir": os.environ["UNODE_TEST_PM2_DIR"], "version": "fixture"}
        target = u.node_info(shutil.which("node"))
        entry = Path(package["dir"]) / "bin/pm2"
        with tempfile.TemporaryDirectory(prefix="unode-restartup-real-") as temp:
            root = Path(temp)
            home = root / "pm2"
            file = root / "Library/LaunchAgents/custom.plist"
            file.parent.mkdir(parents=True)
            file.write_text("old configuration")
            events = root / "starts.jsonl"
            app = root / "app.cjs"
            app.write_text("require('fs').appendFileSync(" + json.dumps(str(events)) +
                           ", JSON.stringify({role:process.env.ROLE,instance:process.env.NODE_APP_INSTANCE})+'\\n'); setInterval(()=>{},1000);\n")
            ecosystem = root / "apps.json"
            ecosystem.write_text(json.dumps({"apps": [
                {"name": "workers", "script": str(app), "exec_mode": "cluster", "instances": 2, "env": {"ROLE": "worker"}},
                {"name": "stopped-job", "script": str(app), "env": {"ROLE": "job"}}]}))
            env = dict(u.node_env(target["path"]), HOME=temp, PM2_HOME=str(home))
            for key in ("PM2_RPC_PORT", "PM2_PUB_PORT", "PM2_PID_FILE_PATH"):
                env.pop(key, None)
            def pm2(*arguments):
                return u.run([target["path"], entry, *arguments], env=env, timeout=30)
            loaded = [True]
            args = u.argparse.Namespace(pm2_home=home, pm2_dir=None, service="custom", settle=3, health_url=None)
            context = {"package": package, "env": env, "file": file, "exists": True, "platform": "launchd",
                       "service": "custom", "jobs": ["gui/501/com.PM2"]}
            def admin(command, environment, log, check=True):
                command = [str(v) for v in command]
                if command[0] == target["path"]:
                    if command[2] == "unstartup":
                        pm2("kill")
                        file.unlink()
                        loaded[0] = False
                    elif command[2] == "startup":
                        file.write_bytes(u.plistlib.dumps({"Label": "com.PM2", "EnvironmentVariables": {"PATH": env["PATH"], "PM2_HOME": str(home)},
                                                          "ProgramArguments": ["/bin/sh", "-c", str(entry) + " resurrect"]}))
                        pm2("resurrect")
                        loaded[0] = True
                return subprocess.CompletedProcess(command, 0, "", "")
            try:
                pm2("start", ecosystem)
                first = u.pm2_snapshot(target["path"], package, home)
                worker = next(a for a in first["apps"] if a["pm2_env"]["exec_mode"] == "cluster_mode")
                instance = str(worker["pm2_env"]["NODE_APP_INSTANCE"])
                pm2("stop", str(worker["pm_id"]))
                pm2("stop", "stopped-job")
                def stopped_starts():
                    rows = [json.loads(line) for line in events.read_text().splitlines()]
                    return sum(row["role"] == "job" or str(row.get("instance")) == instance for row in rows)
                initial_starts = stopped_starts()
                state = u.State(root / "state", root / "config.json")
                state.start({"action": "restartup"})
                with patch.dict(os.environ, env, clear=True), patch.object(u, "startup_authorize"), \
                        patch.object(u, "startup_admin", side_effect=admin), \
                        patch.object(u, "launchd_jobs", side_effect=lambda *a: ["gui/501/com.PM2"] if loaded[0] else []), redirect_stdout(io.StringIO()):
                    result = u.perform_restartup(args, target, state, context)
                self.assertEqual(result["status"], "config-passed")
                self.assertEqual(stopped_starts(), initial_starts, "stopped applications were briefly started")
                after = u.pm2_snapshot(target["path"], package, home)
                self.assertEqual(sum(a["pm2_env"]["status"] == "stopped" for a in after["apps"]), 2)
                self.assertEqual(after["report"]["node_version"], target["version"])
            finally:
                pm2("kill")


if __name__ == "__main__":
    unittest.main()
