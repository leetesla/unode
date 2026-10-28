import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("unode_module", str(ROOT / "unode"))
spec = importlib.util.spec_from_loader(loader.name, loader)
u = importlib.util.module_from_spec(spec)
loader.exec_module(u)


def application(mode="cluster_mode", version="22.20.0", name="api"):
    return {"name": name, "pid": 100, "pm_id": 0, "pm2_env": {
        "name": name, "namespace": "default", "pm_exec_path": "/srv/api.js", "pm_cwd": "/srv",
        "exec_mode": mode, "status": "online", "node_version": version}}


def snapshot(apps, version="22.20.0"):
    return {"report": {"node_version": version, "uid": os.getuid()}, "apps": apps}


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.index = [{"version": "v26.1.0", "lts": False}, {"version": "v24.9.1", "lts": "Krypton"},
                      {"version": "v24.9.0", "lts": "Krypton"}, {"version": "v22.20.0", "lts": "Jod"}]

    def test_resolve_latest_lts_and_partial(self):
        self.assertEqual(u.resolve_version("lts", self.index), "24.9.1")
        self.assertEqual(u.resolve_version("latest", self.index), "26.1.0")
        self.assertEqual(u.resolve_version("24", self.index), "24.9.1")
        self.assertEqual(u.resolve_version("24.9", self.index), "24.9.1")

    def test_exact_version_never_fetches_network(self):
        with patch.object(u, "versions", side_effect=AssertionError("network")):
            self.assertEqual(u.resolve_version("v24.9.0"), "24.9.0")

    def test_invalid_selector_rejected(self):
        for value in ("--help", "24;touch /tmp/pwn", "$(id)", "", "24.x", "24.0.0/../x"):
            with self.subTest(value=value), self.assertRaises(u.UnodeError):
                u.resolve_version(value, self.index)

    def test_major_match_is_not_exact_match(self):
        self.assertTrue(u.matches("24.9.0", "24"))
        self.assertFalse(u.matches("24.9.0", "24.9.1"))


class NodeEnvironmentTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed to inspect the child environment")
    def test_node_child_reports_actual_path_despite_inherited_unode(self):
        node = Path(shutil.which("node")).resolve()
        with patch.dict(os.environ, {"_": "/usr/local/bin/unode"}):
            result = u.run([node, "-p", "JSON.stringify({path:process.execPath,underscore:process.env._})"],
                           env=u.node_env(node))
            data = json.loads(result.stdout)
            self.assertEqual(data["underscore"], data["path"])
            self.assertEqual(os.environ["_"], "/usr/local/bin/unode")


class PM2VerificationTests(unittest.TestCase):
    def test_good_cluster_and_pinned_fork(self):
        before = snapshot([application(), application("fork_mode", "20.0.0", "job")])
        u.verify_original_apps(before, before, "22.20.0")

    def test_missing_worker_rejected(self):
        with self.assertRaises(u.UnodeError):
            u.verify_original_apps(snapshot([application()]), snapshot([]), "22.20.0")

    def test_changed_script_rejected(self):
        before = snapshot([application()])
        after = snapshot([application()])
        after["apps"][0]["pm2_env"]["pm_exec_path"] = "/other.js"
        with self.assertRaises(u.UnodeError):
            u.verify_original_apps(before, after, "22.20.0")

    def test_old_daemon_rejected(self):
        with self.assertRaises(u.UnodeError):
            u.verify_original_apps(snapshot([application()]), snapshot([application()], "20.0.0"), "22.20.0")

    def test_offline_and_wrong_cluster_version_rejected(self):
        for field, value in (("status", "errored"), ("node_version", "20.0.0")):
            after = snapshot([application()])
            after["apps"][0]["pm2_env"][field] = value
            with self.subTest(field=field), self.assertRaises(u.UnodeError):
                u.verify_original_apps(snapshot([application()]), after, "22.20.0")

    def test_stopped_app_is_allowed_and_preserved(self):
        data = snapshot([application()])
        data["apps"][0]["pm2_env"]["status"] = "stopped"
        data["apps"][0]["pid"] = 0
        u.check_original_apps(data)
        u.verify_original_apps(data, data, "22.20.0")

    def test_stopped_app_must_not_be_started(self):
        before, after = snapshot([application()]), snapshot([application()])
        before["apps"][0]["pm2_env"]["status"] = "stopped"
        before["apps"][0]["pid"] = 0
        with self.assertRaises(u.UnodeError):
            u.verify_original_apps(before, after, "22.20.0")

    def test_errored_app_still_blocks_migration(self):
        data = snapshot([application()])
        data["apps"][0]["pm2_env"]["status"] = "errored"
        with self.assertRaises(u.UnodeError):
            u.check_original_apps(data)

    def test_cluster_workers_cannot_swap_online_and_stopped_states(self):
        a, b = application(), application()
        a["pm2_env"]["NODE_APP_INSTANCE"] = 0
        b["pm2_env"]["NODE_APP_INSTANCE"] = 1
        b["pm2_env"]["status"], b["pid"] = "stopped", 0
        before = snapshot([a, b])
        after = json.loads(json.dumps(before))
        after["apps"][0]["pm2_env"]["status"], after["apps"][0]["pid"] = "stopped", 0
        after["apps"][1]["pm2_env"]["status"], after["apps"][1]["pid"] = "online", 100
        with self.assertRaises(u.UnodeError):
            u.verify_original_apps(before, after, "22.20.0")

    def test_missing_pid_does_not_start_daemon(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(u, "run", side_effect=AssertionError("must not call PM2")):
            self.assertIsNone(u.pm2_snapshot("node", None, Path(temp)))


class StateTests(unittest.TestCase):
    def test_config_default_read_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp) / "unode"
            self.assertEqual(u.State(home, Path(temp) / "missing-config.json").config()["manager"], "nvm")
            self.assertFalse(home.exists())

    def test_private_backups_and_last_run(self):
        with tempfile.TemporaryDirectory() as temp:
            state = u.State(Path(temp) / "unode")
            folder = state.start({"process_pm2": False})
            state.finish("passed")
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((folder / "result.json").stat().st_mode), 0o600)
            self.assertEqual(json.loads((state.home / "last-run.json").read_text())["status"], "passed")

    def test_data_directory_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "dest"
            dest.mkdir()
            link = Path(temp) / "link"
            link.symlink_to(dest, target_is_directory=True)
            with self.assertRaises(u.UnodeError):
                u.State(link).private_dir(link)

    def test_lock_prevents_reentry(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "lock"
            with u.lock_file(file):
                with self.assertRaises(u.UnodeError):
                    with u.lock_file(file):
                        pass

    def test_two_users_share_readonly_config_but_not_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "etc/config.json"
            config.parent.mkdir()
            content = '{"schema":1,"manager":"fnm","settle_seconds":12}\n'
            config.write_text(content)
            config.chmod(0o644)
            a, b = u.State(root / "user-a", config), u.State(root / "user-b", config)
            self.assertEqual(a.config(), b.config())
            self.assertEqual(a.config()["manager"], "fnm")
            a.start({"process_pm2": False})
            a.finish("passed")
            self.assertEqual(config.read_text(), content)
            self.assertFalse(b.home.exists())

    def test_invalid_global_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.json"
            for bad in ([], {"schema": 99}, {"schema": 1, "settle_seconds": 0}, {"schema": 1, "settle_seconds": True}):
                config.write_text(json.dumps(bad))
                with self.assertRaises(u.UnodeError):
                    u.State(Path(temp) / "state", config).config()


class MainFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "state"
        self.node = {"version": "22.20.0", "path": "/existing/node"}
        self.shared_config = Path(self.temp.name) / "global-config.json"
        self.shared_config.write_text('{"schema":1,"manager":"nvm","settle_seconds":30}\n')
        self.env = patch.dict(os.environ, {"UNODE_HOME": str(self.home), "UNODE_CONFIG": str(self.shared_config), "PM2_RPC_PORT": "custom-not-used"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_confirm_enter_defaults_yes_and_explicit_no_is_respected(self):
        with patch.object(u, "interactive", return_value=True), patch("builtins.input", return_value="") as prompt:
            self.assertTrue(u.confirm("测试确认"))
            self.assertIn("[Y/n]", prompt.call_args.args[0])
        with patch.object(u, "interactive", return_value=True), patch("builtins.input", return_value="n"):
            self.assertFalse(u.confirm("测试确认"))

    def test_all_upgrade_confirmations_default_yes(self):
        with patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=self.node), \
                patch.object(u.Manager, "available", return_value=True), patch.object(u.Manager, "installed", return_value=self.node), \
                patch.object(u, "confirm", side_effect=[False, False, False, False]) as confirm, redirect_stdout(io.StringIO()):
            self.assertEqual(u.main([self.node["version"], "--m", "nvm"]), 0)
        self.assertEqual(confirm.call_count, 4)
        self.assertTrue(all(call.kwargs.get("default") is True for call in confirm.call_args_list))
        self.assertFalse(self.home.exists())

    def test_answer_no_skips_every_pm2_operation(self):
        with patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=self.node), \
                patch.object(u, "confirm", side_effect=[False, False, True]), \
                patch.object(u, "pm2_package", side_effect=AssertionError("PM2 discovery forbidden")), \
                patch.object(u, "daemon_pid", side_effect=AssertionError("PM2 PID read forbidden")), \
                patch.object(u, "migrate_pm2", side_effect=AssertionError("PM2 mutation forbidden")), \
                patch.object(u, "startup_node_check", return_value={"status": "not-configured", "details": "fixture"}), \
                patch.object(u, "startup_check", side_effect=AssertionError("PM2 service inspection forbidden")):
            self.assertEqual(u.main(["--m", "path", "--node-path", "/existing/node"]), 0)
        data = json.loads((self.home / "last-run.json").read_text())
        self.assertFalse(data["plan"]["process_pm2"])
        self.assertEqual(data["startup"]["status"], "skipped")
        self.assertEqual(json.loads(self.shared_config.read_text()), {"schema": 1, "manager": "nvm", "settle_seconds": 30})
        self.assertFalse((self.home / "config.json").exists())

    def test_cancel_plan_has_no_state_writes(self):
        with patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=self.node), \
                patch.object(u, "confirm", side_effect=[False, False, False]):
            self.assertEqual(u.main(["--m", "path", "--node-path", "/existing/node"]), 0)
        self.assertFalse(self.home.exists())

    def test_check_is_noninteractive_and_has_no_writes(self):
        with patch.object(u, "node_info", return_value=self.node), \
                patch.object(u, "confirm", side_effect=AssertionError("must not prompt")), \
                patch.object(u, "pm2_package", side_effect=AssertionError("PM2 skipped")):
            self.assertEqual(u.main(["--m", "path", "--node-path", "/existing/node", "--check"]), 0)
        self.assertFalse(self.home.exists())

    def test_pm2_only_has_no_manager_menu(self):
        args = u.parser().parse_args(["--pm2"])
        self.assertTrue(u.choose_pm2(args))

    def test_pm2_only_missing_package_is_installed_only_after_confirmation(self):
        pm2_home = Path(self.temp.name) / "pm2"
        package = {"dir": "/isolated/pm2", "version": "7.1.1"}
        with patch.dict(os.environ, {"PM2_RPC_PORT": ""}), \
                patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=self.node), \
                patch.object(u, "confirm", side_effect=[False, True]), patch.object(u, "daemon_pid", return_value=None), \
                patch.object(u, "pm2_package", side_effect=[None, None, package]), \
                patch.object(u, "npm", return_value=subprocess.CompletedProcess([], 0, "", "")) as npm, \
                patch.object(u, "startup_node_check", return_value={"status": "not-configured", "details": "fixture"}), \
                patch.object(u, "pm2_snapshot", side_effect=AssertionError("no daemon to inspect")):
            self.assertEqual(u.main(["--pm2", "--pm2-home", str(pm2_home)]), 0)
            self.assertIn("pm2@latest", npm.call_args[0][1])
        self.assertTrue(json.loads((self.home / "last-run.json").read_text())["plan"]["process_pm2"])

    def test_pm2_only_version_conflict(self):
        with self.assertRaises(u.UnodeError):
            u.main(["24", "--pm2", "--check"])

    def test_pm2_only_initialization_reaches_restartup_with_or_without_empty_daemon(self):
        package = {"dir": "/isolated/pm2", "version": "7.1.1"}
        for live in (False, True):
            with self.subTest(live=live), patch.dict(os.environ, {"PM2_RPC_PORT": ""}), \
                    patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=self.node), \
                    patch.object(u, "confirm", side_effect=[True, True]), \
                    patch.object(u, "daemon_pid", return_value=123 if live else None), \
                    patch.object(u, "pm2_snapshot", return_value=snapshot([], self.node["version"]) if live else None), \
                    patch.object(u, "pm2_package", side_effect=[package, package] if live else [None, None, package]), \
                    patch.object(u, "npm", return_value=subprocess.CompletedProcess([], 0, "", "")) as npm, \
                    patch.object(u, "perform_restartup", return_value={"status": "config-passed", "application_count": 0}) as restartup, \
                    patch.object(u, "startup_node_check", return_value={"status": "match", "node": self.node}), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(u.main(["--pm2", "--pm2-home", str(Path(self.temp.name) / "pm2")]), 0)
                restartup.assert_called_once()
                self.assertEqual(npm.called, not live)
                self.assertEqual(json.loads((self.home / "last-run.json").read_text())["status"], "passed")

    def test_noninteractive_apply_is_rejected(self):
        with patch.object(u, "interactive", return_value=False), self.assertRaises(u.UnodeError):
            u.main(["24"])


    def test_completed_install_checks_startup_even_when_pm2_is_skipped(self):
        result = {"status": "mismatch", "node": {"version": "20.0.0", "path": "/old/node"}}
        with patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", return_value=self.node), \
                patch.object(u, "confirm", side_effect=[False, False, True]), \
                patch.object(u, "startup_node_check", return_value=result) as check, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.main(["--m", "path", "--node-path", "/existing/node", "--service", "custom"]), 2)
        check.assert_called_once_with(self.node, "custom")
        self.assertIn("22.20.0", output.getvalue())
        self.assertIn("20.0.0", output.getvalue())
        self.assertIn("不一致", output.getvalue())
        data = json.loads((self.home / "last-run.json").read_text())
        self.assertEqual(data["startup_node"]["status"], "mismatch")
        self.assertEqual(data["status"], "passed-with-pending-startup")
        self.assertTrue((Path(data["backup"]) / "startup-node-check.json").is_file())

    def test_new_install_compares_against_installed_node(self):
        target = {"version": "24.1.0", "path": "/new/bin/node"}
        result = {"status": "match", "node": target}
        with patch.object(u, "interactive", return_value=True), patch.object(u, "node_info", side_effect=[self.node, target]), \
                patch.object(u, "confirm", side_effect=[False, False, True]), patch.object(u.Manager, "available", return_value=True), \
                patch.object(u.Manager, "installed", return_value=None), patch.object(u.Manager, "install", return_value=target) as install, \
                patch.object(u, "startup_node_check", return_value=result) as check, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.main(["24.1.0", "--m", "n"]), 0)
        install.assert_called_once_with("24.1.0", None)
        check.assert_called_once_with(target, None)
        self.assertIn("版本与本次目标一致", output.getvalue())


class AdapterTests(unittest.TestCase):
    def test_nvm_version_arguments_are_not_shell_interpolated(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = u.Manager("nvm", u.State(Path(temp) / "state"))
            manager.nvm_script = Path(temp) / "nvm.sh"
            manager.nvm_script.write_text("# test")
            with patch.object(u, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as command:
                manager.nvm(["which", "24.9.0"])
                argv = command.call_args[0][0]
                self.assertEqual(argv[-2:], ["which", "24.9.0"])
                self.assertNotIn("24.9.0", argv[4])

    def test_system_apt_exact_package_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = u.Manager("system", u.State(Path(temp)))
            result = subprocess.CompletedProcess([], 0, "nodejs | 1:24.9.0-1nodesource1 | repo\nnodejs | 24.9.1-1 | repo\nnodejs | 22.20.0-1 | repo\n", "")
            with patch.object(manager, "system_tool", return_value="apt-get"), patch.object(u, "run", return_value=result):
                version, meta = manager.system_target("24")
                self.assertEqual(version, "24.9.1")
                self.assertEqual(meta["package"], "24.9.1-1")

    def test_system_does_not_add_repositories(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = u.Manager("system", u.State(Path(temp)))
            with patch.object(manager, "system_tool", return_value="apt-get"), \
                    patch.object(u, "run", return_value=subprocess.CompletedProcess([], 0, "", "")), self.assertRaises(u.UnodeError):
                manager.system_target("24")


class InstallTests(unittest.TestCase):
    def install(self, prefix):
        config = Path(prefix).parent / "shared config"
        return subprocess.run(["bash", str(ROOT / "install.sh"), "--prefix", str(prefix), "--config-dir", str(config), "--skip-deps"], text=True, capture_output=True)

    @unittest.skipUnless(os.uname().sysname == "Darwin", "real installer platform dependency check exercised on macOS")
    def test_install_to_space_path_and_repeat(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp) / "a directory"
            for _ in range(2):
                result = self.install(prefix)
                self.assertEqual(result.returncode, 0, result.stderr)
                command = prefix / "bin/unode"
                self.assertTrue(command.is_symlink())
                self.assertTrue(command.resolve().with_name("pm2-helper.cjs").is_file())
                check = subprocess.run([str(command), "--version"], capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)
                self.assertIn("unode " + u.VERSION, check.stdout)
                config = prefix.parent / "shared config/config.json"
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o644)
                self.assertEqual(stat.S_IMODE(config.parent.stat().st_mode), 0o755)
                self.assertEqual(command.resolve().with_name("config-path").read_text().strip(), str(config))

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS installer")
    def test_existing_global_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "shared config/config.json"
            config.parent.mkdir()
            content = '{"schema":1,"manager":"fnm","settle_seconds":12}\n'
            config.write_text(content)
            result = self.install(root / "prefix")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(config.read_text(), content)

    @unittest.skipUnless(os.uname().sysname == "Darwin" and shutil.which("node"), "Node plus macOS installer")
    def test_installed_command_loads_custom_shared_config_path(self):
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp) / "prefix"
            result = self.install(prefix)
            self.assertEqual(result.returncode, 0, result.stderr)
            node = subprocess.check_output(["node", "-p", "process.execPath"], text=True).strip()
            env = dict(os.environ, UNODE_HOME=str(Path(temp) / "private-state"))
            env.pop("UNODE_CONFIG", None)
            result = subprocess.run([str(prefix / "bin/unode"), "--m", "path", "--node-path", node, "--check"], capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(prefix.parent / "shared config/config.json"), result.stdout)
            self.assertFalse((Path(temp) / "private-state").exists())

    @unittest.skipUnless(os.uname().sysname == "Darwin", "macOS installer")
    def test_existing_symlink_is_replaced_without_modifying_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "prefix/bin").mkdir(parents=True)
            sentinel = root / "sentinel"
            sentinel.write_text("untouched")
            (root / "prefix/bin/unode").symlink_to(sentinel)
            result = self.install(root / "prefix")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sentinel.read_text(), "untouched")

    def test_missing_relative_prefix_rejected(self):
        result = self.install("relative")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("绝对路径", result.stderr)

    def test_missing_prefix_argument_rejected(self):
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--prefix"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)


class ShowStartupTests(unittest.TestCase):
    def test_show_dispatch_needs_no_config_node_or_terminal(self):
        with patch.object(u, "State", side_effect=AssertionError("must not read config")), \
                patch.object(u, "show_startup", return_value=0) as show:
            self.assertEqual(u.main(["show", "startup", "--service", "custom"]), 0)
        show.assert_called_once_with("custom")

    def test_mac_uses_plist_label_and_falls_back_to_user_domain(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "Library/LaunchAgents/custom.plist"
            file.parent.mkdir(parents=True)
            file.write_bytes(u.plistlib.dumps({"Label": "com.PM2", "ProgramArguments": ["/runtime/node"]},
                                            fmt=u.plistlib.FMT_BINARY))
            missing = subprocess.CompletedProcess([], 113, "", 'Bad request.\nCould not find service "com.PM2" in domain')
            results = [missing, subprocess.CompletedProcess([], 0, "state = running\n", ""), missing]
            with patch.object(u.platform, "system", return_value="Darwin"), patch.object(u.Path, "home", return_value=Path(temp)), \
                    patch.object(u, "startup_reference", return_value=(None, "目标")), \
                    patch.object(u, "launchd_autostart_status", return_value={"status": "enabled", "details": "fixture"}), \
                    patch.object(u, "startup_node_check", return_value={"status": "inspected", "node": {"version": "22.20.0", "path": "/runtime/node"}}), \
                    patch.object(u, "run", side_effect=results) as run, redirect_stdout(io.StringIO()) as output:
                self.assertEqual(u.main(["show", "startup", "--service", "custom.plist"]), 0)
            self.assertEqual(run.call_args_list[0].args[0], ["launchctl", "print", "gui/{}/com.PM2".format(os.getuid())])
            self.assertEqual(run.call_args_list[1].args[0], ["launchctl", "print", "user/{}/com.PM2".format(os.getuid())])
            self.assertIn(str(file), output.getvalue())
            self.assertIn("22.20.0", output.getvalue())
            self.assertIn("state = running", output.getvalue())

    def test_mac_absent_oneshot_explains_lifecycle_without_claiming_success(self):
        config = {"Label": "com.PM2", "LaunchOnlyOnce": True}
        missing = subprocess.CompletedProcess([], 113, "", 'Bad request.\nCould not find service "com.PM2" in domain for user gui: 501')
        with patch.object(u.platform, "system", return_value="Darwin"), \
                patch.object(u, "launchagent_config", return_value=(Path("/unused/pm2.plist"), config)), \
                patch.object(u, "show_startup_node", return_value=0), \
                patch.object(u, "launchd_autostart_status", return_value={"status": "enabled", "details": "fixture"}), \
                patch.object(u, "run", return_value=missing) as run, \
                patch.object(u, "startup_admin", side_effect=AssertionError("read only")), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.show_startup(), 0)
        self.assertEqual(len(run.call_args_list), 3)
        self.assertTrue(all(call.args[0][:2] == ["launchctl", "print"] for call in run.call_args_list))
        self.assertIn("一次性启动任务", output.getvalue())
        self.assertIn("开机自启：已启用（当前用户登录后自动启动）", output.getvalue())
        self.assertIn("两个独立状态", output.getvalue())
        self.assertNotIn("Bad request", output.getvalue())
        self.assertNotIn("无权查询", output.getvalue())

    def test_mac_absent_regular_service_is_not_reported_as_oneshot(self):
        missing = subprocess.CompletedProcess([], 113, "", 'Could not find service "custom" in domain')
        with patch.object(u, "run", return_value=missing), redirect_stdout(io.StringIO()) as output:
            result = u.launchd_service_status("custom")
            u.display_launchd_status({"Label": "custom"}, result)
        self.assertEqual(result["status"], "not-loaded")
        self.assertIn("服务当前未加载", output.getvalue())
        self.assertNotIn("一次性", output.getvalue())

    def test_mac_permission_error_is_not_hidden_by_oneshot_config(self):
        missing = subprocess.CompletedProcess([], 113, "", 'Could not find service "com.PM2" in domain')
        denied = subprocess.CompletedProcess([], 1, "", "Operation not permitted")
        with patch.object(u.platform, "system", return_value="Darwin"), \
                patch.object(u, "launchagent_config", return_value=(Path("/unused/pm2.plist"), {"Label": "com.PM2", "LaunchOnlyOnce": True})), \
                patch.object(u, "show_startup_node", return_value=0), \
                patch.object(u, "launchd_autostart_status", return_value={"status": "enabled", "details": "fixture"}), \
                patch.object(u, "run", side_effect=[missing, denied, missing]), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.show_startup(), 2)
        self.assertIn("Operation not permitted", output.getvalue())
        self.assertIn("权限或会话查询失败", output.getvalue())
        self.assertNotIn("当前未注册", output.getvalue())

    def test_mac_ambiguous_bad_request_is_a_query_error(self):
        with patch.object(u, "run", return_value=subprocess.CompletedProcess([], 1, "", "Bad request.")):
            result = u.launchd_service_status("com.PM2")
        self.assertEqual(result["status"], "unknown")

    def test_linux_displays_inactive_service_and_dropins(self):
        results = [subprocess.CompletedProcess([], 0, "# /etc/systemd/system/custom.service\n# drop-in\n", ""),
                   subprocess.CompletedProcess([], 3, "Active: inactive (dead)\n", "")]
        with patch.object(u.platform, "system", return_value="Linux"), patch.object(u.shutil, "which", return_value="/bin/systemctl"), \
                patch.object(u, "startup_reference", return_value=(None, "目标")), \
                patch.object(u, "startup_node_check", return_value={"status": "manual", "details": "fixture"}), \
                patch.object(u, "run", side_effect=results) as run, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.main(["show", "startup", "--service", "custom"]), 2)
        self.assertEqual(run.call_args_list[0].args[0], ["systemctl", "--no-pager", "cat", "--", "custom.service"])
        self.assertIn("drop-in", output.getvalue())
        self.assertIn("inactive", output.getvalue())

    def test_missing_or_invalid_plist_is_reported_without_querying_service(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(u.platform, "system", return_value="Darwin"), \
                patch.object(u.Path, "home", return_value=Path(temp)), patch.object(u, "run", side_effect=AssertionError("no service query")):
            with self.assertRaises(u.UnodeError):
                u.show_startup("missing")
            file = Path(temp) / "Library/LaunchAgents/custom.plist"
            file.parent.mkdir(parents=True)
            for data in (b"<?xml version='1.0'?><plist><dict>", u.plistlib.dumps({"Label": ""})):
                file.write_bytes(data)
                with self.assertRaises(u.UnodeError):
                    u.show_startup("custom")

    def test_service_path_traversal_is_rejected(self):
        for system in ("Darwin", "Linux"):
            with self.subTest(system=system), patch.object(u.platform, "system", return_value=system), \
                    patch.object(u.shutil, "which", return_value="/bin/systemctl"), \
                    patch.object(u, "run", side_effect=AssertionError("invalid input must not execute")), self.assertRaises(u.UnodeError):
                u.show_startup("../../other")


class LaunchdAutostartTests(unittest.TestCase):
    def status(self, output='disabled services = {\n"com.PM2" => enabled\n}', code=0, **fields):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "pm2.plist"
            file.write_bytes(b"fixture")
            config = {"Label": "com.PM2", "RunAtLoad": True, **fields}
            with patch.object(u, "run", return_value=subprocess.CompletedProcess([], code, output, "query error" if code else "")) as run:
                result = u.launchd_autostart_status(file, config)
            self.assertEqual(run.call_args.args[0], ["launchctl", "print-disabled", "gui/{}".format(os.getuid())])
            return result

    def test_explicit_enable_overrides_disabled_plist(self):
        self.assertEqual(self.status(Disabled=True)["status"], "enabled")

    def test_disabled_override_takes_precedence_over_run_at_load(self):
        for value in ("disabled", "true"):
            with self.subTest(value=value):
                self.assertEqual(self.status('disabled services = {\n"com.PM2" => ' + value + '\n}')["status"], "disabled")

    def test_absent_override_uses_plist_default(self):
        self.assertEqual(self.status("disabled services = {}")["status"], "enabled")
        self.assertEqual(self.status("disabled services = {}", Disabled=True)["status"], "disabled")
        self.assertEqual(self.status('disabled services = {\n"comXPM2" => disabled\n}')["status"], "enabled")

    def test_old_boolean_override_format(self):
        self.assertEqual(self.status('disabled services = {\n"com.PM2" => false;\n}')["status"], "enabled")

    def test_run_at_load_is_required_unless_keepalive_starts_at_load(self):
        self.assertEqual(self.status(RunAtLoad=False)["status"], "not-configured")
        self.assertEqual(self.status(RunAtLoad=False, KeepAlive=True)["status"], "enabled")
        self.assertEqual(self.status(RunAtLoad=False, OnDemand=False)["status"], "enabled")

    def test_query_failure_or_unrecognized_output_never_means_enabled(self):
        for output, code in (("", 1), ("new unknown format", 0), ('disabled services = {\n"com.PM2" => perhaps\n}', 0)):
            with self.subTest(output=output, code=code):
                self.assertEqual(self.status(output, code)["status"], "unknown")

    def test_session_restrictions_are_not_ignored(self):
        self.assertEqual(self.status(LimitLoadToSessionType="Background")["status"], "unknown")
        self.assertEqual(self.status(LimitLoadToSessionType=["Aqua", "Background"])["status"], "enabled")

    def test_show_preserves_unknown_enablement_even_for_oneshot(self):
        with patch.object(u.platform, "system", return_value="Darwin"), \
                patch.object(u, "launchagent_config", return_value=(Path("/unused/pm2.plist"), {"Label": "com.PM2", "LaunchOnlyOnce": True})), \
                patch.object(u, "show_startup_node", return_value=0), \
                patch.object(u, "launchd_autostart_status", return_value={"status": "unknown", "details": "query failure"}), \
                patch.object(u, "launchd_service_status", return_value={"status": "not-loaded", "outputs": [], "failures": []}), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.show_startup(), 2)
        self.assertIn("开机自启：无法确认启用状态", output.getvalue())
        self.assertIn("query failure", output.getvalue())


class StartupReferenceTests(unittest.TestCase):
    def test_prefers_completed_install_over_current_shell_node(self):
        target = {"version": "22.23.4", "path": "/new/bin/node"}
        for status in ("passed", "passed-with-pending-startup"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp:
                file = Path(temp) / "last-run.json"
                file.write_text(json.dumps({"status": status, "target": target}))
                with patch.dict(os.environ, {"UNODE_HOME": temp}), \
                        patch.object(u, "node_info", side_effect=AssertionError("must use saved install target")):
                    self.assertEqual(u.startup_reference(), (target, "最近安装目标"))

    def test_falls_back_to_current_node_when_no_completed_target(self):
        node = {"version": "22.23.4", "path": "/current/node"}
        for record in (None, {"status": "failed", "target": {"version": "24.0.0"}}, {"status": "passed", "target": {"version": 24}}):
            with self.subTest(record=record), tempfile.TemporaryDirectory() as temp:
                if record:
                    (Path(temp) / "last-run.json").write_text(json.dumps(record))
                with patch.dict(os.environ, {"UNODE_HOME": temp}), patch.object(u.shutil, "which", return_value=node["path"]), \
                        patch.object(u, "node_info", return_value=node):
                    self.assertEqual(u.startup_reference(), (node, "当前终端"))

    def test_show_reports_mismatch_and_returns_pending_exit_code(self):
        target = {"version": "22.23.4", "path": "/new/bin/node"}
        result = {"status": "mismatch", "node": {"version": "22.20.0", "path": "/old/bin/node"}}
        with patch.object(u, "startup_reference", return_value=(target, "最近安装目标")), \
                patch.object(u, "startup_node_check", return_value=result) as check, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(u.show_startup_node("custom"), 2)
        check.assert_called_once_with(target, "custom")
        for text in ("最近安装目标 Node：22.23.4", "启动配置 Node：22.20.0", "与最近安装目标不一致"):
            self.assertIn(text, output.getvalue())

    def test_missing_comparison_target_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"UNODE_HOME": temp}), \
                patch.object(u.shutil, "which", return_value=None):
            self.assertEqual(u.startup_reference(), (None, "目标"))
        result = {"status": "inspected", "node": {"version": "22.20.0", "path": "/old/node"}}
        with redirect_stdout(io.StringIO()) as output:
            u.display_startup_node(result)
        self.assertIn("未找到可比较的目标版本", output.getvalue())


class StartupNodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.node = self.bin / "node"
        self.node.write_text("#!/bin/sh\n")
        self.node.chmod(0o755)
        self.pm2 = self.bin / "pm2"
        self.pm2.write_text("#!/usr/bin/env node\n")
        self.target = {"version": "22.20.0", "path": str(self.node)}

    def tearDown(self):
        self.temp.cleanup()

    def mac_check(self, version="22.20.0", arguments=None):
        file = self.root / "Library/LaunchAgents/custom.plist"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(u.plistlib.dumps({"Label": "com.PM2", "EnvironmentVariables": {"PATH": str(self.bin)},
                                          "ProgramArguments": arguments or ["/bin/sh", "-c", u.shlex.quote(str(self.pm2)) + " resurrect"]}))
        with patch.object(u.platform, "system", return_value="Darwin"), patch.object(u.Path, "home", return_value=self.root), \
                patch.object(u, "node_info", return_value={"version": version, "path": str(self.node)}) as probe, \
                patch.object(u, "run", side_effect=AssertionError("must not execute PM2 or a shell")):
            result = u.startup_node_check(self.target, "custom")
        return result, probe

    def test_mac_matches_and_detects_old_startup_version(self):
        for version, status in (("22.20.0", "match"), ("20.0.0", "mismatch")):
            result, probe = self.mac_check(version)
            self.assertEqual(result["status"], status)
            probe.assert_called_once_with(str(self.node), check=False)

    def test_explicit_shebang_overrides_path(self):
        self.pm2.write_text("#!/pinned/bin/node\n")
        result, probe = self.mac_check()
        self.assertEqual(result["status"], "match")
        probe.assert_called_once_with("/pinned/bin/node", check=False)

    def test_shell_expressions_are_not_executed_or_claimed_verified(self):
        result, probe = self.mac_check(arguments=["/bin/sh", "-c", "$(touch /tmp/forbidden) resurrect"])
        self.assertEqual(result["status"], "manual")
        probe.assert_not_called()

    def test_missing_pm2_is_broken_configuration(self):
        self.pm2.unlink()
        result, probe = self.mac_check()
        self.assertEqual(result["status"], "manual")
        probe.assert_not_called()

    def test_linux_reads_service_environment_instead_of_current_shell(self):
        properties = "LoadState=loaded\nEnvironment=PATH={0}\nExecStart={{ path={1} ; argv[]={1} resurrect ; }}\n".format(self.bin, self.pm2)
        with patch.object(u.platform, "system", return_value="Linux"), \
                patch.object(u.shutil, "which", side_effect=lambda name, path=None: "/bin/systemctl" if name == "systemctl" else str(self.node)), \
                patch.object(u, "run", return_value=subprocess.CompletedProcess([], 0, properties, "")), \
                patch.object(u, "node_info", return_value=self.target) as probe:
            result = u.startup_node_check(self.target, "custom")
        self.assertEqual(result["status"], "match")
        probe.assert_called_once_with(str(self.node), check=False)

    def test_missing_service_is_unconfigured(self):
        with patch.object(u.platform, "system", return_value="Linux"), \
                patch.object(u.shutil, "which", return_value="/bin/systemctl"), \
                patch.object(u, "run", return_value=subprocess.CompletedProcess([], 1, "LoadState=not-found\n", "")):
            self.assertEqual(u.startup_node_check(self.target)["status"], "not-configured")

    def test_unknown_environment_file_is_not_claimed_verified(self):
        with patch.object(u.platform, "system", return_value="Linux"), \
                patch.object(u.shutil, "which", return_value="/bin/systemctl"), \
                patch.object(u, "run", return_value=subprocess.CompletedProcess([], 0, "LoadState=loaded\nEnvironmentFiles=/etc/custom.env\n", "")):
            self.assertEqual(u.startup_node_check(self.target)["status"], "manual")

    def test_stale_systemd_properties_are_not_claimed_verified(self):
        with patch.object(u.platform, "system", return_value="Linux"), \
                patch.object(u.shutil, "which", return_value="/bin/systemctl"), \
                patch.object(u, "run", return_value=subprocess.CompletedProcess([], 0, "LoadState=loaded\nNeedDaemonReload=yes\n", "")):
            self.assertEqual(u.startup_node_check(self.target)["status"], "manual")

    def test_comparison_highlights_status_and_honors_no_color(self):
        for status, color in (("match", "32"), ("mismatch", "31")):
            output = io.StringIO()
            with patch.dict(os.environ, {"TERM": "xterm"}), patch.object(u.sys, "stdout", output), \
                    patch.object(output, "isatty", return_value=True), patch.dict(os.environ) as environment:
                environment.pop("NO_COLOR", None)
                u.display_startup_node({"status": status, "node": self.target}, self.target)
            self.assertIn("\033[1;" + color + "m", output.getvalue())
        with patch.dict(os.environ, {"NO_COLOR": "1"}), redirect_stdout(io.StringIO()) as output:
            u.display_startup_node({"status": "match", "node": self.target}, self.target)
        self.assertNotIn("\033", output.getvalue())


class StartupTests(unittest.TestCase):
    def check(self, overrides=None, dump=None):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            entry = home / "pm2"
            entry.write_text("#!/bin/sh\n")
            entry.chmod(0o755)
            app = home / "app.js"
            app.write_text("// fixture")
            (home / "dump.pm2").write_text(json.dumps(dump if dump is not None else [{"name": "api", "pm_exec_path": str(app), "pm_cwd": temp, "exec_interpreter": "node"}]))
            values = {"LoadState": "loaded", "UnitFileState": "enabled", "ActiveState": "active", "User": u.pwd.getpwuid(os.getuid()).pw_name,
                      "Environment": "PATH=/runtime/bin PM2_HOME=" + temp, "MainPID": "123", "NeedDaemonReload": "no",
                      "ExecStart": "{ path=" + str(entry) + " ; argv[]=" + str(entry) + " resurrect ; }"}
            values.update(overrides or {})
            output = "\n".join(key + "=" + val for key, val in values.items())
            result = subprocess.CompletedProcess([], 0, output, "")
            with patch.object(u.platform, "system", return_value="Linux"), \
                    patch.object(u.shutil, "which", side_effect=lambda name, path=None: "/bin/systemctl" if name == "systemctl" else "/runtime/bin/node"), \
                    patch.object(u, "run", return_value=result), patch.object(u, "daemon_pid", return_value=123), \
                    patch.object(u, "node_info", return_value={"version": "22.20.0", "path": "/runtime/bin/node"}):
                return u.startup_check(home, {"version": "22.20.0", "path": "/runtime/bin/node"})

    def test_correct_systemd_configuration(self):
        self.assertEqual(self.check()["status"], "config-passed")

    def test_empty_dump_is_valid_for_service_without_applications(self):
        self.assertEqual(self.check(dump=[])["status"], "config-passed")

    def test_wrong_user_and_old_pid_need_review(self):
        result = self.check({"User": "other-user", "MainPID": "999"})
        self.assertEqual(result["status"], "needs-review")
        self.assertTrue(any("用户" in issue for issue in result["issues"]))
        self.assertTrue(any("PID" in issue for issue in result["issues"]))

    def test_runtime_enabled_does_not_count_as_persistent(self):
        result = self.check({"UnitFileState": "enabled-runtime"})
        self.assertEqual(result["status"], "needs-review")
        self.assertTrue(any("临时" in issue for issue in result["issues"]))

    def test_daemon_reload_and_inactive_need_review(self):
        result = self.check({"NeedDaemonReload": "yes", "ActiveState": "inactive"})
        self.assertEqual(result["status"], "needs-review")

    def test_corrupt_dump_needs_review_not_traceback(self):
        result = self.check(dump=[42])
        self.assertEqual(result["status"], "needs-review")

    def test_unconfigured_unit_is_explicit(self):
        self.assertEqual(self.check({"LoadState": "not-found"})["status"], "not-configured")


if __name__ == "__main__":
    unittest.main()
