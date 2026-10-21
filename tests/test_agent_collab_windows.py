"""Windows support logic that can be checked on any host.

The Job Object backend itself needs Windows; tests/windows_runtime_checks.py
exercises it on the Windows CI runner.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_agent_collab_runtime as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class _WindowsOs:
    """os with name "nt", so one module takes its Windows branches.

    Patching os.name itself would also switch pathlib to WindowsPath.
    """

    name = "nt"

    def __getattr__(self, attribute):
        return getattr(os, attribute)


def windows(module):
    return mock.patch.object(module, "os", _WindowsOs())


class FakeJobs:
    def __init__(self):
        self.calls = []

    def process_start_time(self, pid):
        self.calls.append(("start_time", pid))
        return "133700000000000000"

    def group_alive(self, pgid):
        self.calls.append(("alive", pgid))
        return True

    def terminate_group(self, pgid, timeout_seconds):
        self.calls.append(("terminate", pgid, timeout_seconds))
        return {"outcome": "killed", "quiescent": True, "pgid": pgid}

    def adopt(self, pid):
        self.calls.append(("adopt", pid))

    def pid_alive(self, pid):
        self.calls.append(("pid_alive", pid))
        return False


class ProviderResolutionTests(TestCase):
    def test_windows_requires_a_native_executable_and_ignores_the_current_directory(self):
        safety = fixtures.load_peer().SAFETY
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shims, native, cwd = root / "npm", root / "native", root / "repo"
            for directory in (shims, native, cwd):
                directory.mkdir()
            (shims / "codex.cmd").write_text("@echo off\r\n")
            (native / "codex.exe").write_bytes(b"MZ")
            (native / "claude.exe").write_bytes(b"MZ")
            (cwd / "claude.exe").write_bytes(b"MZ")
            env = {"PATHEXT": ".COM;.EXE;.BAT;.CMD"}
            # Only PATH entries count, so the repo's claude.exe is never chosen.
            with windows(safety), mock.patch.dict(os.environ, env):
                with self.assertRaisesRegex(FileNotFoundError, "not a native executable"):
                    safety.resolve_cli("codex", os.pathsep.join([str(shims), str(native)]))
                self.assertEqual(
                    safety.resolve_cli("codex", os.pathsep.join([f'"{native}"', str(shims)])),
                    str(native / "codex.exe"),
                )
                self.assertEqual(safety.resolve_cli("claude", str(native)), str(native / "claude.exe"))
                with self.assertRaisesRegex(FileNotFoundError, "not found on PATH"):
                    safety.resolve_cli("claude", str(shims))
                self.assertEqual(
                    safety.native_command(["codex", "exec", "-"], {"PATH": str(native)}),
                    [str(native / "codex.exe"), "exec", "-"],
                )

    def test_posix_keeps_the_bare_command_for_exec_lookup(self):
        safety = fixtures.load_peer().SAFETY
        self.assertEqual(safety.native_command(["codex", "exec"], {"PATH": ""}), ["codex", "exec"])


class WindowsProcessGroupTests(TestCase):
    def test_identity_liveness_and_termination_use_the_job_backend(self):
        safety = fixtures.load_peer().SAFETY
        jobs = FakeJobs()
        with windows(safety), mock.patch.object(safety, "_load_sibling", return_value=jobs):
            identity = safety.process_identity(4242)
            self.assertEqual(identity, {
                "kind": "windows_process", "pid": 4242, "pgid": 4242,
                "boot_id": "not_available", "start_time": "133700000000000000",
            })
            safety.validate_process_identity(identity)
            self.assertTrue(safety.process_group_alive(4242))
            self.assertEqual(safety.terminate_process_group(4242, kill_timeout_seconds=3)["outcome"], "killed")
            self.assertFalse(safety.pid_alive(4242))
            with self.assertRaisesRegex(ValueError, "own process group"):
                safety.terminate_process_group(os.getpid())
        self.assertIn(("terminate", 4242, 3), jobs.calls)

    def test_provider_tracker_schema_accepts_windows_identities(self):
        schema = json.loads((fixtures.RUNTIME_ROOT / "schemas" / "provider-process.schema.json").read_text())
        identity = schema["properties"]["process_identity"]["oneOf"][1]
        self.assertEqual(
            set(identity["properties"]["kind"]["enum"]),
            fixtures.load_peer().SAFETY.PROCESS_IDENTITY_KINDS,
        )

    def test_popen_group_adopts_the_root_and_detaches_with_breakaway(self):
        safety = fixtures.load_peer().SAFETY
        jobs = FakeJobs()
        flags = {"CREATE_NEW_PROCESS_GROUP": 0x200, "CREATE_NO_WINDOW": 0x08000000,
                 "CREATE_BREAKAWAY_FROM_JOB": 0x01000000}
        process = SimpleNamespace(pid=77, kill=mock.Mock(), wait=mock.Mock())
        with (
            windows(safety),
            mock.patch.object(safety, "_load_sibling", return_value=jobs),
            mock.patch.multiple(safety.subprocess, create=True, **flags),
            mock.patch.object(safety.subprocess, "Popen", return_value=process) as popen,
        ):
            self.assertIs(safety.popen_group(["x"], cwd="/"), process)
            self.assertEqual(popen.call_args.kwargs["creationflags"], 0x200)
            self.assertIs(safety.popen_group(["x"], detached=True), process)
            self.assertEqual(popen.call_args.kwargs["creationflags"], 0x200 | 0x08000000 | 0x01000000)
            self.assertNotIn("start_new_session", popen.call_args.kwargs)
            self.assertEqual(jobs.calls, [("adopt", 77), ("adopt", 77)])

            popen.side_effect = PermissionError(5, "Access is denied")
            with self.assertRaisesRegex(PermissionError, "forbid breakaway"):
                safety.popen_group(["x"], detached=True)

            popen.side_effect = None
            with (
                mock.patch.object(jobs, "adopt", side_effect=OSError("assign failed")),
                self.assertRaisesRegex(OSError, "assign failed"),
            ):
                safety.popen_group(["x"])
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with()

    def test_windows_gate_passes_one_inheritable_handle(self):
        safety = fixtures.load_peer().SAFETY
        fake_msvcrt = SimpleNamespace(get_osfhandle=lambda fd: 1000 + fd, open_osfhandle=lambda handle, flags: handle - 1000)

        class StartupInfo:
            def __init__(self):
                self.lpAttributeList = {"handle_list": []}

        inherited = []
        with (
            windows(safety),
            mock.patch.dict(sys.modules, {"msvcrt": fake_msvcrt}),
            mock.patch.object(safety.subprocess, "STARTUPINFO", StartupInfo, create=True),
            mock.patch.object(safety.os, "set_handle_inheritable", lambda handle, value: inherited.append((handle, value)),
                              create=True),
        ):
            kwargs = {}
            command = safety.gated_command(["codex.exe", "exec"], 5, kwargs)
            self.assertEqual(command[:2], [sys.executable, "-c"])
            self.assertEqual(command[2], safety.WINDOWS_GATE_CODE)
            self.assertEqual(command[3:], ["1005", "codex.exe", "exec"])
            self.assertEqual(kwargs["startupinfo"].lpAttributeList["handle_list"], [1005])
            self.assertNotIn("pass_fds", kwargs)
            self.assertEqual(inherited, [(1005, True)])
            self.assertEqual(safety.receive_gate(1005), 5)

    def test_posix_gate_releases_the_command_only_after_one_byte(self):
        safety = fixtures.load_peer().SAFETY
        read_fd, write_fd = os.pipe()
        kwargs = {}
        command = safety.gated_command([sys.executable, "-c", "print('started')"], read_fd, kwargs)
        process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, **kwargs)
        os.close(read_fd)
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0.3)
        os.write(write_fd, b"1")
        os.close(write_fd)
        self.assertEqual(process.communicate(timeout=10)[0], "started\n")
        self.assertEqual(process.returncode, 0)


class WindowsLaunchPolicyTests(TestCase):
    def test_codex_safe_mode_fails_closed_on_native_windows(self):
        safety = fixtures.load_peer().SAFETY
        with windows(safety):
            result = safety.sandbox_preflight(Path.cwd())
        self.assertEqual(result["status"], "sandbox_unavailable")
        self.assertEqual(result["backend"], "windows")

    def test_host_cli_guard_adds_a_cmd_shim_on_windows(self):
        peer = fixtures.load_peer()
        with tempfile.TemporaryDirectory() as tmp, windows(peer):
            bin_dir = peer.make_host_cli_guard(Path(tmp), "claude")
            shim = (bin_dir / "claude.cmd").read_bytes()
            script = (bin_dir / "claude").read_bytes()
        self.assertIn(b"@exit /b 64\r\n", shim)
        self.assertNotIn(b"\r", script)

    def test_claude_hooks_use_exec_form_without_a_shell(self):
        peer = fixtures.load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = peer.build_peer_command(
                fixtures.request(peer="claude", host="codex", peer_model="claude-opus-5-5", peer_effort="xhigh"),
                "brief", root, fixtures.PEER_SCHEMA, root / "peer.raw.json", {}, "git",
            )
            settings = json.loads(command.args[command.args.index("--settings") + 1])
            hooks = [
                settings["hooks"]["PreToolUse"][0]["hooks"][0],
                settings["hooks"]["Stop"][0]["hooks"][0],
            ]
            for hook in hooks:
                with self.subTest(hook=hook["args"][:2]):
                    self.assertEqual(hook["command"], sys.executable)
                    self.assertIsInstance(hook["args"], list)
                    self.assertNotIn("shell", hook)
            guard = hooks[0]
            self.assertEqual(subprocess.run([guard["command"], *guard["args"]], capture_output=True).returncode, 0)
