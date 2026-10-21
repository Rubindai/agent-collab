"""Windows support logic that can be checked on any host.

The Job Object backend itself needs Windows; tests/windows_runtime_checks.py
exercises it on the Windows CI runner.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
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
    CREATE_SUSPENDED = 0x4

    def __init__(self):
        self.calls = []

    def resume(self, pid):
        self.calls.append(("resume", pid))

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
            self.assertEqual(popen.call_args.kwargs["creationflags"], 0x200 | 0x4)
            self.assertIs(safety.popen_group(["x"], detached=True), process)
            self.assertEqual(popen.call_args.kwargs["creationflags"], 0x200 | 0x4 | 0x08000000 | 0x01000000)
            self.assertNotIn("start_new_session", popen.call_args.kwargs)
            # The suspended root is assigned to its job before it runs.
            self.assertEqual(jobs.calls, [("adopt", 77), ("resume", 77), ("adopt", 77), ("resume", 77)])

            popen.side_effect = PermissionError(5, "Access is denied")
            with self.assertRaisesRegex(PermissionError, "forbid breakaway"):
                safety.popen_group(["x"], detached=True)

            popen.side_effect = None
            for step in ("adopt", "resume"):
                process.kill.reset_mock()
                process.wait.reset_mock()
                with (
                    self.subTest(step=step),
                    mock.patch.object(jobs, step, side_effect=OSError(f"{step} failed")),
                    self.assertRaisesRegex(OSError, f"{step} failed"),
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
            self.assertEqual(command[:4], [sys.executable, "-I", "-S", "-c"])
            self.assertEqual(command[4], safety.WINDOWS_GATE_CODE)
            self.assertEqual(command[5:], ["1005", "codex.exe", "exec"])
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


class CrossPlatformIdentityTests(TestCase):
    def test_another_platforms_identity_is_unknown_not_dead(self):
        safety = fixtures.load_peer().SAFETY
        local = safety.process_identity(os.getpid())
        self.assertEqual(local["kind"], safety.local_identity_kind())
        self.assertEqual(safety.process_identity_state(local), "matching")
        for kind in sorted(safety.PROCESS_IDENTITY_KINDS - {local["kind"]}):
            with self.subTest(kind=kind):
                foreign = {**local, "kind": kind}
                self.assertTrue(safety.identity_is_foreign(foreign))
                self.assertEqual(safety.process_identity_state(foreign), "unknown")
        with windows(safety):
            self.assertEqual(safety.process_identity_state(local), "unknown")

    def test_host_refuses_to_manage_a_run_started_on_another_platform(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            request = fixtures.request()
            data = fixtures.valid_peer_process(run_dir, Path(tmp), Path(tmp) / "guard.json")
            host.validate_peer_process(data, request, run_dir)
            kind = next(iter(host.SAFETY.PROCESS_IDENTITY_KINDS - {host.SAFETY.local_identity_kind()}))
            data["process_identity"] = {**data["process_identity"], "kind": kind}
            with self.assertRaisesRegex(host.ArtifactValidationError, "started on another platform"):
                host.validate_peer_process(data, request, run_dir)

    def test_unknown_identities_never_release_the_guard(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            request = fixtures.request()
            process_info = {"pid": 4242, "pgid": 4242, "process_identity": {"kind": "linux_proc"}}
            provider = {"status": "running", "cleanup_outcome": None, "pgid": 4243,
                        "process_identity": {"kind": "linux_proc"}}
            with (
                mock.patch.object(host, "load_provider_process", return_value=provider),
                mock.patch.object(host.SAFETY, "process_identity_state", return_value="unknown"),
                mock.patch.object(host.SAFETY, "process_group_alive", return_value=False),
                mock.patch.object(host.SAFETY, "terminate_process_group") as terminate,
            ):
                cleanup = host.terminate_tracked_process_tree(process_info, run_dir, request)
                with self.assertRaisesRegex(host.ArtifactValidationError, "cannot be verified"):
                    host.require_process_tree_quiescent(process_info, run_dir, request)
            terminate.assert_not_called()
            self.assertFalse(cleanup["quiescent"])

    def test_windows_trampoline_keeps_ntstatus_exit_codes(self):
        code = fixtures.load_peer().SAFETY.WINDOWS_GATE_CODE
        self.assertIn("code - 2**32 if code >= 2**31 else code", code)


class SnapshotReaderTests(TestCase):
    def fake_git(self, tmp, body):
        bin_dir = Path(tmp) / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "git"
        fake.write_text("#!/bin/sh\n" + body)
        fake.chmod(0o755)
        return mock.patch.dict(os.environ, {"PATH": os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")])})

    def test_posix_git_stays_in_the_callers_process_group(self):
        # Cancelling the wrapper's group must also stop a snapshot in progress.
        snapshot = fixtures.load_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pgid"
            with self.fake_git(tmp, f"ps -o pgid= -p $$ > '{marker}'\n"):
                snapshot._git_output_digest(Path(tmp), ["diff"], time.monotonic() + 10)
                self.assertEqual(snapshot._run_git(Path(tmp), ["status"], time.monotonic() + 10).returncode, 0)
            self.assertEqual(int(marker.read_text()), os.getpgrp())

    def test_timeout_returns_promptly_while_a_helper_holds_the_pipe(self):
        snapshot = fixtures.load_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "helper.pid"
            with self.fake_git(tmp, f"sleep 30 & echo $! > '{marker}'\nsleep 30\n"):
                for run in (snapshot._git_output_digest, snapshot._run_git):
                    with self.subTest(run=run.__name__):
                        started = time.monotonic()
                        with self.assertRaises(TimeoutError):
                            run(Path(tmp), ["diff"], time.monotonic() + 1)
                        self.assertLess(time.monotonic() - started, 4)
                        try:
                            os.kill(int(marker.read_text()), 9)
                        except (ProcessLookupError, ValueError):
                            pass

    def test_an_expired_deadline_stays_a_timeout(self):
        snapshot = fixtures.load_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "file.txt").write_text("content")
            expired = time.monotonic() - 1
            with self.assertRaises(TimeoutError):
                snapshot._run_git(Path(tmp), ["status"], expired)
            with self.assertRaises(TimeoutError):
                snapshot._path_signature(Path(tmp) / "file.txt", expired)
            with mock.patch.object(snapshot, "_hash_file", side_effect=TimeoutError("deadline")):
                with self.assertRaises(TimeoutError):
                    snapshot._filesystem_entries(Path(tmp), None, time.monotonic() + 10)


class WindowsStdioTests(TestCase):
    def test_entry_points_use_utf8_with_lf_output_and_universal_input(self):
        strict_json = fixtures.load_peer().STRICT_JSON
        streams = {name: mock.Mock() for name in ("stdin", "stdout", "stderr")}
        with windows(strict_json), mock.patch.multiple(strict_json.sys, **streams):
            strict_json.use_utf8_stdio()
        streams["stdin"].reconfigure.assert_called_once_with(encoding="utf-8", errors="strict")
        streams["stdout"].reconfigure.assert_called_once_with(encoding="utf-8", errors="strict", newline="\n")
        streams["stderr"].reconfigure.assert_called_once_with(
            encoding="utf-8", errors="backslashreplace", newline="\n"
        )

    def test_every_entry_point_sets_up_stdio(self):
        root = fixtures.RUNTIME_ROOT / "scripts"
        for name in ("host.py", "peer.py", "claude_telemetry.py", "safety.py"):
            with self.subTest(name=name):
                main = (root / name).read_text().split("\ndef main(", 1)[1].split("\n\n\n", 1)[0]
                self.assertIn("use_utf8_stdio()", main)


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
