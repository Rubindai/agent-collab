"""Windows-only checks of Agent Collab process supervision and locking.

The unittest discovery suite targets Linux and macOS. CI runs this file on a
Windows runner with `python tests/windows_runtime_checks.py -v`.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import unittest.mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "tools" / "agent-collab" / "scripts"


def load(name):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(f"agent_collab_{name}_windows_check", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SAFETY = load("safety")
STRICT_JSON = load("strict_json")
JOBS = SAFETY._load_sibling("windows_jobs") if os.name == "nt" else None


def wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def python_command(code):
    return [sys.executable, "-c", textwrap.dedent(code)]


@unittest.skipUnless(os.name == "nt", "Windows only")
class JobSupervisionChecks(unittest.TestCase):
    def start_root(self, code, **kwargs):
        process = SAFETY.popen_group(
            python_command(code), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs
        )
        self.addCleanup(lambda: SAFETY.terminate_process_group(process.pid))
        return process

    def test_structure_sizes_match_the_win32_layout(self):
        if sys.maxsize > 2**32:
            self.assertEqual(JOBS.ctypes.sizeof(JOBS._BasicLimitInformation), 64)
            self.assertEqual(JOBS.ctypes.sizeof(JOBS._ExtendedLimitInformation), 144)
        self.assertEqual(JOBS.ctypes.sizeof(JOBS._BasicAccountingInformation), 48)

    def test_detached_descendants_stay_in_the_job_until_it_is_terminated(self):
        root = self.start_root("""
            import subprocess, sys, time
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                     creationflags=subprocess.DETACHED_PROCESS)
            try:
                subprocess.Popen([sys.executable, "-c", "pass"],
                                 creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB).wait()
                breakaway = "allowed"
            except PermissionError:
                breakaway = "denied"
            print(child.pid, breakaway, flush=True)
            time.sleep(120)
        """)
        grandchild, breakaway = root.stdout.readline().split()
        grandchild = int(grandchild)
        self.assertEqual(breakaway, "denied")
        self.assertTrue(SAFETY.process_group_alive(root.pid))
        self.assertTrue(SAFETY.pid_alive(grandchild))
        result = SAFETY.terminate_process_group(root.pid)
        self.assertTrue(result["quiescent"], result)
        self.assertEqual(result["outcome"], "killed")
        # The job's active count drops as each member starts exiting; its
        # process object is signaled a moment later.
        self.assertTrue(wait_until(lambda: not SAFETY.pid_alive(grandchild), 5))
        root.wait(timeout=5)
        self.assertFalse(SAFETY.process_group_alive(root.pid))

    def test_root_exit_kills_the_remaining_members(self):
        root = self.start_root("""
            import subprocess, sys
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                     creationflags=subprocess.DETACHED_PROCESS)
            print(child.pid, flush=True)
        """)
        grandchild = int(root.stdout.readline())
        root.wait(timeout=10)
        self.assertTrue(wait_until(lambda: not SAFETY.pid_alive(grandchild)))
        self.assertFalse(SAFETY.process_group_alive(root.pid))
        self.assertEqual(SAFETY.terminate_process_group(root.pid)["outcome"], "not_running")

    def test_identity_is_stable_and_ends_with_the_process(self):
        root = self.start_root("import time; time.sleep(120)")
        identity = SAFETY.process_identity(root.pid)
        SAFETY.validate_process_identity(identity)
        self.assertEqual(identity["kind"], "windows_process")
        self.assertEqual(SAFETY.process_identity(root.pid), identity)
        self.assertEqual(SAFETY.process_identity_state(identity), "matching")
        SAFETY.terminate_process_group(root.pid)
        root.wait(timeout=5)
        with self.assertRaises(ProcessLookupError):
            SAFETY.process_identity(root.pid)
        self.assertEqual(SAFETY.process_identity_state(identity), "dead_or_replaced")
        self.assertFalse(SAFETY.pid_alive(root.pid))

    def test_gated_command_waits_for_the_gate_and_relays_the_exit_status(self):
        read_fd, write_fd = os.pipe()
        kwargs = {}
        command = SAFETY.gated_command(
            python_command("import sys; print('ran', flush=True); sys.exit(7)"), read_fd, kwargs
        )
        process = SAFETY.popen_group(command, stdout=subprocess.PIPE, text=True, **kwargs)
        os.close(read_fd)
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0.5)
        self.assertTrue(SAFETY.process_group_alive(process.pid))
        os.write(write_fd, b"1")
        os.close(write_fd)
        self.assertEqual(process.communicate(timeout=20)[0].strip(), "ran")
        self.assertEqual(process.returncode, 7)
        self.assertTrue(wait_until(lambda: not SAFETY.process_group_alive(process.pid)))

    def test_a_closed_gate_never_starts_the_command(self):
        read_fd, write_fd = os.pipe()
        kwargs = {}
        command = SAFETY.gated_command(python_command("print('ran')"), read_fd, kwargs)
        process = SAFETY.popen_group(command, stdout=subprocess.PIPE, text=True, **kwargs)
        os.close(read_fd)
        os.close(write_fd)
        self.assertEqual(process.communicate(timeout=20)[0], "")
        self.assertEqual(process.returncode, 125)

    def test_a_venv_interpreter_and_its_children_stay_in_the_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(Path(tmp) / "venv")], check=True)
            venv_python = Path(tmp) / "venv" / "Scripts" / "python.exe"
            script = f"""
                import json, subprocess, sys
                sys.path.insert(0, {str(SCRIPTS)!r})
                import safety
                root = safety.popen_group([sys.executable, "-c", (
                    "import subprocess, sys, time;"
                    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'],"
                    " creationflags=subprocess.DETACHED_PROCESS);"
                    "print(child.pid, flush=True); time.sleep(120)")], stdout=subprocess.PIPE, text=True)
                grandchild = int(root.stdout.readline())
                result = safety.terminate_process_group(root.pid)
                root.wait(timeout=10)
                print(json.dumps({{
                    "redirector": sys._base_executable != sys.executable,
                    "quiescent": result["quiescent"],
                    "grandchild_alive": safety.pid_alive(grandchild),
                }}))
            """
            completed = subprocess.run(
                [str(venv_python), "-c", textwrap.dedent(script)], capture_output=True, text=True, timeout=60
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            # The venv python.exe is a redirector that starts the real
            # interpreter as its child; suspended creation keeps both inside.
            self.assertEqual(json.loads(completed.stdout.splitlines()[-1]),
                             {"redirector": True, "quiescent": True, "grandchild_alive": False})

    def test_a_live_root_without_a_visible_job_is_not_quiescent(self):
        root = self.start_root("import time; time.sleep(120)")
        with unittest.mock.patch.object(JOBS, "_open_job", return_value=None):
            result = SAFETY.terminate_process_group(root.pid)
        self.assertEqual(result["outcome"], "job_not_visible")
        self.assertFalse(result["quiescent"])

    def test_ntstatus_exit_codes_survive_the_trampoline(self):
        read_fd, write_fd = os.pipe()
        kwargs = {}
        command = SAFETY.gated_command(python_command("import os; os._exit(-1073741819)"), read_fd, kwargs)
        process = SAFETY.popen_group(command, **kwargs)
        os.close(read_fd)
        os.write(write_fd, b"1")
        os.close(write_fd)
        self.assertEqual(process.wait(timeout=20), 0xC0000005)

    def test_detached_root_receives_its_startup_gate(self):
        read_fd, write_fd = os.pipe()
        kwargs = {}
        token = SAFETY.pass_gate(read_fd, kwargs)
        code = f"""
            import importlib.util, os, sys
            spec = importlib.util.spec_from_file_location("safety", {str(SCRIPTS / "safety.py")!r})
            safety = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(safety)
            fd = safety.receive_gate({token})
            print(os.read(fd, 1).decode(), flush=True)
        """
        try:
            process = SAFETY.popen_group(
                python_command(code), detached=True, stdout=subprocess.PIPE, text=True, **kwargs
            )
        except PermissionError as exc:
            self.skipTest(f"this runner's job forbids breakaway: {exc}")
        os.close(read_fd)
        os.write(write_fd, b"1")
        os.close(write_fd)
        self.assertEqual(process.communicate(timeout=20)[0].strip(), "1")


@unittest.skipUnless(os.name == "nt", "Windows only")
class PortableFileChecks(unittest.TestCase):
    def test_registry_owner_survives_historical_release(self):
        host = load("host")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with unittest.mock.patch.object(host.REGISTRY, "workspace_guard_path", return_value=root / "guard.json"):
                host.REGISTRY.acquire_workspace_guard(root, "reused", "codex", root / "run")
                old = host.REGISTRY.read_workspace_guard(root, "reused")[1]
                host.REGISTRY.release_workspace_guard(root, "reused", owner=old)
                host.REGISTRY.acquire_workspace_guard(root, "reused", "codex", root / "run")
                original = (root / "guard.json").read_bytes()
                with self.assertRaisesRegex(SystemExit, "owner mismatch"):
                    host.REGISTRY.release_workspace_guard(root, "reused", owner=old)
                self.assertEqual((root / "guard.json").read_bytes(), original)

    def test_review_receipt_uses_native_paths_and_utf8(self):
        host = load("host")
        review = host.CONFIG.REVIEW
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp).resolve()
            owner = {"run_id":"run", "host":"codex", "owner_token":"a" * 32,
                     "run_dir":str(run), "repo_root":str(run)}
            documents = {name:None for name in review.EVIDENCE_FILES}
            documents["host-first-pass.json"] = {"summary":"Unicode: 中文", "claims":[]}
            paths = review.prepare(run, owner, documents)
            review.verify(run, Path(paths["review_dir"]), owner, documents)
            self.assertIn("中文", review.markdown(documents))
            self.assertNotIn(b"\r\n", Path(paths["synthesis_input"]).read_bytes())

    def test_locks_exclude_other_processes_until_released(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "guard.lock"
            descriptor = STRICT_JSON.open_lock_file(path)
            probe = python_command(f"""
                import importlib.util
                spec = importlib.util.spec_from_file_location("sj", {str(SCRIPTS / "strict_json.py")!r})
                sj = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sj)
                from pathlib import Path
                fd = sj.open_lock_file(Path({str(path)!r}))
                print(sj.try_lock(fd))
            """)
            try:
                self.assertTrue(STRICT_JSON.try_lock(descriptor))
                self.assertEqual(subprocess.run(probe, capture_output=True, text=True).stdout.strip(), "False")
                STRICT_JSON.unlock(descriptor)
                self.assertEqual(subprocess.run(probe, capture_output=True, text=True).stdout.strip(), "True")
            finally:
                os.close(descriptor)

    def test_entry_point_stdio_is_utf8_with_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out.txt"
            code = textwrap.dedent(f"""
                import importlib.util, sys
                spec = importlib.util.spec_from_file_location("sj", {str(SCRIPTS / "strict_json.py")!r})
                sj = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(sj)
                sj.use_utf8_stdio()
                print(sys.stdin.read().strip() + " café 測試 📁")
            """)
            with output.open("wb") as handle:
                subprocess.run([sys.executable, "-c", code], input="入力".encode("utf-8"), stdout=handle, check=True)
            self.assertEqual(output.read_bytes(), "入力 café 測試 📁\n".encode("utf-8"))

    def test_artifacts_are_written_with_lf_line_endings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.json"
            STRICT_JSON.write(path, {"a": [1, 2]})
            self.assertNotIn(b"\r", path.read_bytes())
            with STRICT_JSON.open_private_text(Path(tmp) / "log.txt") as handle:
                handle.write("one\ntwo\n")
            self.assertEqual((Path(tmp) / "log.txt").read_bytes(), b"one\ntwo\n")

    def test_batch_shims_are_rejected_before_native_executables(self):
        with tempfile.TemporaryDirectory() as tmp:
            shims, native = Path(tmp) / "shims", Path(tmp) / "native"
            shims.mkdir()
            native.mkdir()
            (shims / "fakecli.cmd").write_text("@exit /b 0\r\n")
            (native / "fakecli.exe").write_bytes(Path(sys.executable).read_bytes())
            with self.assertRaisesRegex(FileNotFoundError, "not a native executable"):
                SAFETY.resolve_cli("fakecli", os.pathsep.join([str(shims), str(native)]))
            self.assertEqual(
                SAFETY.resolve_cli("fakecli", os.pathsep.join([str(native), str(shims)])).lower(),
                str(native / "fakecli.exe").lower(),
            )

    def test_doctor_reports_windows_as_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            env = {**os.environ, "XDG_STATE_HOME": str(Path(tmp) / "state"), "XDG_CONFIG_HOME": str(Path(tmp) / "config")}
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "host.py"), "doctor", "--repo-root", tmp],
                capture_output=True, text=True, encoding="utf-8", env=env,
            )
            report = json.loads(completed.stdout)
            self.assertTrue(report["checks"]["platform"]["ok"], report["checks"]["platform"])
            self.assertEqual(report["checks"]["workspace_isolation"]["value"]["mode"], "direct")

    def test_snapshot_digest_reads_git_output_without_select(self):
        snapshot = load("snapshot")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True)
            (Path(tmp) / "file.txt").write_text("content\n")
            digest = snapshot._git_output_digest(Path(tmp), ["ls-files", "--others"], time.monotonic() + 30)
            self.assertRegex(digest, r"^sha256=[0-9a-f]{64} bytes=\d+$")


if __name__ == "__main__":
    unittest.main()
