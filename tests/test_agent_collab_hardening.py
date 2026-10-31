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

import agent_collab_test_support as fixtures

setUpModule = fixtures.setUpModule
tearDownModule = fixtures.tearDownModule


class SettingsParsingTests(TestCase):
    def test_empty_boolean_environment_value_is_rejected_not_false(self):
        host = fixtures.load_host()
        with self.assertRaisesRegex(ValueError, "expected boolean"):
            host.CONFIG.normalize_settings({"safe_mode": ""}, "environment AGENT_COLLAB_SAFE_MODE")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                mock.patch.object(host.CONFIG, "default_global_settings_path", return_value=root / "g"),
                mock.patch.object(host.CONFIG, "default_local_settings_path", return_value=root / "l"),
            ):
                resolved = host.CONFIG.resolve_settings(root, {"AGENT_COLLAB_SAFE_MODE": ""})
        self.assertTrue(resolved["environment_errors"])
        self.assertEqual(resolved["sources"]["safe_mode"], "builtin")

    def test_integer_settings_accept_only_ascii_digits(self):
        host = fixtures.load_host()
        self.assertEqual(host.CONFIG.normalize_settings({"max_local_subagents": "8"}, "env")["max_local_subagents"], 8)
        for value in ("٨", "8.0", "-1", True, " "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.CONFIG.normalize_settings({"max_local_subagents": value}, "env")
        with self.assertRaises(ValueError):
            host.CONFIG.normalize_settings({"history_retained_runs": "٥"}, "env")

    def test_timeout_and_cli_numbers_accept_only_ascii_decimals(self):
        host = fixtures.load_host()
        self.assertEqual(host.CONFIG.normalize_timeout("2700"), 2700)
        self.assertEqual(host.CONFIG.normalize_timeout(" 90.5 "), 90.5)
        for value in ("٢٧٠٠", "２７００", "2_700", "2.7e3", "inf", "nan", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.CONFIG.normalize_timeout(value)
        self.assertEqual(host.CONFIG.cli_integer("8"), 8)
        self.assertEqual(host.CONFIG.cli_number("90.5"), 90.5)
        for parse, value in ((host.CONFIG.cli_integer, "٨"), (host.CONFIG.cli_integer, "1_0"), (host.CONFIG.cli_number, "2.7e3")):
            with self.subTest(value=value), self.assertRaises(host.argparse.ArgumentTypeError):
                parse(value)

    def test_claude_tools_sentinel_must_be_lowercase_default(self):
        host = fixtures.load_host()
        peer = fixtures.load_peer()
        self.assertEqual(host.CONFIG.validate_claude_tools("default", "test"), "default")
        for value in ("DEFAULT", "Default"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "lowercase literal"):
                    host.CONFIG.validate_claude_tools(value, "test")
                with self.assertRaisesRegex(peer.RequestValidationError, "lowercase literal"):
                    peer.validate_request(fixtures.request(claude_tools=value))


class StateRootTests(TestCase):
    def test_inherited_claude_plugin_data_does_not_select_the_state_root(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "plugins" / "data" / "other-plugin"
            xdg = Path(tmp) / "xdg-state"
            env = {"CLAUDE_PLUGIN_DATA": str(foreign), "XDG_STATE_HOME": str(xdg)}
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("AGENT_COLLAB_STATE_HOME", None)
                with mock.patch.object(host.CONFIG, "resource_root", return_value=Path(tmp) / "elsewhere"):
                    self.assertEqual(host.CONFIG.default_state_root(), xdg / "agent-collab")

    def test_active_run_is_found_through_the_worktree_guard_from_another_run_root(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            other_root = root / "other-host-runs"
            run_dir = other_root / "agent-collab-test"
            run_dir.mkdir(parents=True)
            (run_dir / "host-request.json").write_text("{}")
            guard = {"run_id": "agent-collab-test", "run_dir": str(run_dir)}
            with mock.patch.object(host.REGISTRY, "read_workspace_guard", return_value=(root / "guard", guard)):
                self.assertEqual(host.RUNS.guarded_run_dir(repo, "agent-collab-test"), run_dir.resolve())
                self.assertIsNone(host.RUNS.guarded_run_dir(repo, "agent-collab-other"))
                resolved = host.RUNS.resolve_run_reference(repo, "agent-collab-test", root / "my-runs")
                self.assertEqual(resolved, run_dir.resolve())
                args = SimpleNamespace(repo_root=repo, run_root=root / "my-runs", run="agent-collab-test")
                self.assertEqual(host.RUNS.resolve_run_context(args), (repo.resolve(), other_root.resolve(), run_dir.resolve()))


class IsolationBoundaryTests(TestCase):
    def isolation(self):
        return fixtures.load_host().CONFIG.ISOLATION

    def test_sandbox_command_does_not_bind_the_run_directory_writable(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "home").mkdir()
            (root / "repo").mkdir()
            (root / "run").mkdir()
            with mock.patch.dict(os.environ, {"HOME": str(root / "home")}):
                workspace = isolation.Workspace(root / "repo", root / "run")
            workspace.mounts = [root / "m-home", root / "m-repo"]
            credential = root / "home" / ".codex" / "auth.json"
            command = workspace.command(["codex"], root / "tmp", [credential])
            pairs = [command[index + 1:index + 3] for index, item in enumerate(command) if item == "--bind"]
            self.assertNotIn([str((root / "run").resolve()), str((root / "run").resolve())], pairs)
            home_bind = pairs.index([str(root / "m-home"), str((root / "home").resolve())])
            self.assertGreater(pairs.index([str(credential), str(credential)]), home_bind)

    def test_credential_passthrough_selects_the_peer_products_regular_file(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            codex_auth = home / ".codex" / "auth.json"
            claude_auth = home / ".claude" / ".credentials.json"
            self.assertEqual(isolation.credential_passthrough("codex", {"HOME": str(home)}), [])
            codex_auth.parent.mkdir()
            codex_auth.write_text("{}")
            claude_auth.parent.mkdir()
            claude_auth.symlink_to(codex_auth)
            env = {"HOME": str(home)}
            self.assertEqual(isolation.credential_passthrough("codex", env), [codex_auth.resolve()])
            self.assertEqual(isolation.credential_passthrough("claude", env), [])
            custom = home / "custom-codex"
            custom.mkdir()
            (custom / "auth.json").write_text("{}")
            self.assertEqual(
                isolation.credential_passthrough("codex", {"HOME": str(home), "CODEX_HOME": str(custom)}),
                [(custom / "auth.json").resolve()],
            )

    def test_workspace_exit_ignores_a_rewritten_state_file(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            own = root / "own-scratch"
            own.mkdir()
            victim = Path(tempfile.mkdtemp(prefix="agent-collab-cow-victim-", dir="/tmp"))
            try:
                (victim / "keep").write_text("untouched")
                workspace = isolation.Workspace(root, run)
                workspace.scratch = own
                (run / "isolation-state.json").write_text(json.dumps({
                    "schema_version": "3.0", "scratch": str(victim),
                    "mounts": [str(victim / "home" / "merged"), str(victim / "repo" / "merged")],
                    "status": "mounted",
                }))
                workspace.__exit__(None, None, None)
                self.assertEqual((victim / "keep").read_text(), "untouched")
                self.assertFalse(own.exists())
                state = json.loads((run / "isolation-state.json").read_text())
                self.assertEqual(state["scratch"], str(own))
                self.assertEqual(state["status"], "cleaned")
            finally:
                import shutil
                shutil.rmtree(victim, ignore_errors=True)

    def test_state_writer_survives_a_stale_temporary_file(self):
        isolation = self.isolation()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "isolation-state.json"
            (Path(tmp) / "isolation-state.json.tmp").write_text("stale")
            isolation._write_state(path, {"status": "mounted"})
            self.assertEqual(json.loads(path.read_text()), {"status": "mounted"})
            with self.assertRaises(ValueError):
                isolation._write_state(path, {"value": float("nan")})

    def test_real_overlay_discards_writes_and_passes_credentials_through(self):
        isolation = self.isolation()
        if sys.platform != "linux":
            self.skipTest("the isolated workspace backend is Linux-only")
        with tempfile.TemporaryDirectory() as probe_root:
            if isolation.preflight(Path(probe_root))["status"] != "available":
                self.skipTest("fuse-overlayfs and bwrap are not usable on this host")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, repo, scratch_tmp = root / "home", root / "repo", root / "provider-tmp"
            run = home / "state" / "runs" / "agent-collab-test"
            for directory in (home, repo, run, scratch_tmp):
                directory.mkdir(parents=True)
            (repo / "tracked.txt").write_text("original")
            (run / "host-request.json").write_text("host-owned")
            credential = home / ".codex" / "auth.json"
            credential.parent.mkdir()
            credential.write_text("old-token")
            script = (
                f"printf changed > {repo}/tracked.txt; printf new > {repo}/new.txt; "
                f"printf forged > {run}/host-request.json; printf rotated > {credential}; "
                f"printf out > {scratch_tmp}/peer-output.json"
            )
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                with isolation.Workspace(repo, run) as workspace:
                    command = workspace.command(["sh", "-c", script], scratch_tmp, [credential])
                    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    scratch = workspace.scratch
            self.assertEqual((repo / "tracked.txt").read_text(), "original")
            self.assertFalse((repo / "new.txt").exists())
            self.assertEqual((run / "host-request.json").read_text(), "host-owned")
            self.assertEqual(credential.read_text(), "rotated")
            self.assertEqual((scratch_tmp / "peer-output.json").read_text(), "out")
            self.assertFalse(scratch.exists())
            self.assertEqual(json.loads((run / "isolation-state.json").read_text())["status"], "cleaned")


def _schema(name):
    return json.loads((fixtures.RUNTIME_ROOT / "schemas" / name).read_text())


def _all_claim_status_enums(node):
    found = []
    if isinstance(node, dict):
        props = node.get("properties", {})
        if {"claim", "status", "evidence"} <= set(props) and "enum" in props["status"]:
            found.append(set(props["status"]["enum"]))
        for value in node.values():
            found += _all_claim_status_enums(value)
    elif isinstance(node, list):
        for value in node:
            found += _all_claim_status_enums(value)
    return found


class SchemaRuntimeParityTests(TestCase):
    def test_codex_config_schema_pattern_matches_owned_keys(self):
        safety = fixtures.load_host().CONFIG.SAFETY
        owned = sorted(safety.AGENT_COLLAB_OWNED_CODEX_CONFIG_KEYS)
        expected = "^(?!(?:" + "|".join(owned) + ")(?:\\.|=))[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)*="
        settings = _schema("settings.schema.json")
        request = _schema("host-request.schema.json")
        patterns = [
            settings["properties"]["settings"]["properties"]["codex_config"]["items"]["pattern"],
            request["properties"]["codex_config"]["items"]["pattern"],
        ]
        self.assertEqual(patterns, [expected, expected])

    def test_schemas_and_runtime_agree_on_sample_values(self):
        import jsonschema

        host = fixtures.load_host()
        settings_props = _schema("settings.schema.json")["properties"]["settings"]["properties"]
        request_props = _schema("host-request.schema.json")["properties"]

        def schema_accepts(schema, value):
            try:
                jsonschema.validate(value, schema)
            except jsonschema.ValidationError:
                return False
            return True

        def runtime_accepts(function, *args):
            try:
                function(*args)
            except ValueError:
                return False
            return True

        for model in ("claude-opus-5-5", "claude-fable-5", "fable", "us.anthropic.claude-fable-5-v1:0",
                      "anthropic/claude-fable-5", "claude-fables-5", "afable"):
            with self.subTest(model=model):
                self.assertEqual(
                    schema_accepts(settings_props["claude_model"], model),
                    not host.CONFIG.is_fable_model(model),
                )
        for tools in ("default", "DEFAULT", "Default", "Read,Grep", "default,Bash", "Read Default"):
            with self.subTest(tools=tools):
                runtime = runtime_accepts(host.CONFIG.validate_claude_tools, tools, "test")
                self.assertEqual(schema_accepts(settings_props["claude_tools"], tools), runtime)
                self.assertEqual(schema_accepts(request_props["claude_tools"], tools), runtime)
        for entry in ("foo=true", "mcp_servers.x.enabled=false", "model_provider=\"x\"", "profiles.a.model=\"m\"",
                      "openai_base_url=\"u\"", "tools.web_search.context_size=\"low\"", "agents=\"x\""):
            with self.subTest(entry=entry):
                runtime = runtime_accepts(host.CONFIG.normalize_codex_config, [entry], "test")
                self.assertEqual(schema_accepts(settings_props["codex_config"]["items"], entry), runtime)

    def test_claim_status_and_mode_enums_match_runtime_constants(self):
        host = fixtures.load_host()
        peer = fixtures.load_peer()
        state = fixtures.load_state()
        for name in ("host-first-pass.schema.json", "helper-reports.schema.json", "host-synthesis.schema.json",
                     "adjudicator-report.schema.json", "claim-matrix.schema.json", "peer-report.schema.json"):
            with self.subTest(schema=name):
                enums = _all_claim_status_enums(_schema(name))
                self.assertTrue(enums)
                for enum in enums:
                    self.assertEqual(enum, set(host.ARTIFACTS.CLAIM_STATUSES))
                    self.assertEqual(enum, set(peer.CLAIM_STATUSES))
        self.assertEqual(set(host.CONFIG.MODES), set(peer.MODES))
        self.assertEqual(set(host.CONFIG.MODES), set(state.MODES))
        self.assertEqual(set(_schema("host-request.schema.json")["properties"]["mode"]["enum"]), set(host.CONFIG.MODES))
        self.assertEqual(host.CONFIG.ORIGINS, peer.ORIGINS)
        self.assertEqual(host.CONFIG.ORIGINS, state.ORIGINS)
        self.assertEqual(
            set(_schema("adjudicator-report.schema.json")["properties"]["recommended_verdict"]["enum"]),
            set(host.ARTIFACTS.RECOMMENDED_VERDICTS),
        )


class AvailabilityCaptureTests(TestCase):
    def test_probe_stop_capture_is_private_under_a_permissive_umask(self):
        availability = fixtures.load_availability()
        captured = {}

        def runner(command, **kwargs):
            settings = json.loads(command[command.index("--settings") + 1])
            hook = settings["hooks"]["Stop"][0]["hooks"][0]
            env = kwargs["env"]
            previous = os.umask(0o022)
            try:
                subprocess.run([hook["command"], *hook["args"]],
                               input=json.dumps({"hook_event_name": "Stop", "effort": {"level": "xhigh"}}),
                               text=True, env=env, check=True)
            finally:
                os.umask(previous)
            path = Path(env["AGENT_COLLAB_EFFORT_CAPTURE"])
            captured["mode"] = path.stat().st_mode & 0o777
            return subprocess.CompletedProcess(command, 0, json.dumps({
                "type": "result", "result": "AVAILABLE", "modelUsage": {"claude-opus-5-5": {}},
            }), "")

        with tempfile.TemporaryDirectory() as tmp:
            result = availability.claude_availability(
                "claude-opus-5-5", "xhigh", "2.1.305", Path(tmp), 30, runner=runner, env=dict(os.environ),
            )
        self.assertEqual(captured.get("mode"), 0o600)
        self.assertEqual(result["status"], "available", result)


class ProviderSchemaTests(TestCase):
    def test_provider_schema_uses_only_the_documented_strict_subset(self):
        # OpenAI strict structured outputs document only pattern/format for
        # strings; Claude accepts the same portable subset.
        allowed = {"$schema", "$id", "title", "description", "type", "properties", "required",
                   "additionalProperties", "items", "enum", "const", "anyOf", "pattern", "format",
                   "minItems", "maxItems"}

        def walk(node, path="root"):
            if isinstance(node, dict):
                for key, value in node.items():
                    if path.endswith(".properties"):
                        walk(value, f"{path}.{key}")
                        continue
                    self.assertIn(key, allowed, f"{path}.{key}")
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}[{index}]")

        walk(_schema("peer-report-provider.schema.json"))


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
                        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


class SnapshotCoverageTests(TestCase):
    def setUp(self):
        self.snapshot = fixtures.load_snapshot()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        for name in ("a.txt", "b.txt", "c.txt"):
            (self.repo / name).write_text(name)
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def changed(self, mutate):
        before = self.snapshot.mutation_snapshot(self.repo)
        mutate()
        after = self.snapshot.mutation_snapshot(self.repo)
        return self.snapshot.diff_snapshots(before, after)

    def test_read_only_git_status_is_not_a_mutation(self):
        os.utime(self.repo / "a.txt", (1, 1))
        result = self.changed(lambda: _git(self.repo, "status", "--porcelain"))
        self.assertFalse(result["changed"], result)

    def test_assume_unchanged_and_skip_worktree_edits_are_detected(self):
        _git(self.repo, "update-index", "--assume-unchanged", "a.txt")
        _git(self.repo, "update-index", "--skip-worktree", "b.txt")
        result = self.changed(lambda: ((self.repo / "a.txt").write_text("edited"),
                                       (self.repo / "b.txt").write_text("edited")))
        self.assertTrue(result["changed"])
        self.assertIn("a.txt", result["changed_paths"])
        self.assertIn("b.txt", result["changed_paths"])

    def test_setting_assume_unchanged_is_detected(self):
        result = self.changed(lambda: _git(self.repo, "update-index", "--assume-unchanged", "a.txt"))
        self.assertTrue(result["changed"])

    def test_permission_only_change_is_detected(self):
        os.chmod(self.repo / "c.txt", 0o644)
        result = self.changed(lambda: os.chmod(self.repo / "c.txt", 0o600))
        self.assertTrue(result["changed"])
        self.assertIn("c.txt", result["changed_paths"])

    def test_textconv_cannot_hide_a_further_edit_to_a_dirty_file(self):
        (self.repo / ".gitattributes").write_text("*.txt diff=blank\n")
        _git(self.repo, "add", ".gitattributes")
        _git(self.repo, "commit", "-qm", "attrs")
        _git(self.repo, "config", "diff.blank.textconv", "true")
        (self.repo / "a.txt").write_text("dirty once")
        result = self.changed(lambda: (self.repo / "a.txt").write_text("dirty twice"))
        self.assertTrue(result["changed"])

    def test_inherited_git_dir_does_not_redirect_the_snapshot(self):
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        _git(other, "init", "-q")
        with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
            data = self.snapshot.mutation_snapshot(self.repo)
        self.assertEqual(data["mode"], "git")
        self.assertEqual(data["sections"]["tracked_count"], 3)

    def test_non_utf8_filenames_serialize_in_git_and_filesystem_modes(self):
        strict_json = fixtures.load_host().CONFIG.STRICT_JSON
        name = os.fsdecode(b"bad-\xff.txt")
        try:
            (self.repo / name).write_text("x")
        except OSError:
            self.skipTest("this filesystem rejects non-UTF-8 names (for example APFS)")
        git_data = self.snapshot.mutation_snapshot(self.repo)
        strict_json.dumps(git_data).encode("utf-8")
        self.assertEqual(git_data["sections"]["untracked_count"], 1)
        plain = Path(self.tmp.name) / "plain"
        plain.mkdir()
        (plain / name).write_text("x")
        fs_data = self.snapshot.mutation_snapshot(plain)
        self.assertEqual(fs_data["mode"], "filesystem")
        strict_json.dumps(fs_data).encode("utf-8")
        out = Path(self.tmp.name) / "snapshot.txt"
        self.snapshot.write_workspace_snapshot(self.repo, out)
        out.read_text(encoding="utf-8")


class StrictJSONEdgeTests(TestCase):
    def test_lone_surrogates_and_huge_integers_raise_strict_errors(self):
        strict_json = fixtures.load_host().CONFIG.STRICT_JSON
        for text in ('"\\ud800"', '{"\\udfff": 1}', "1" * 5000):
            with self.subTest(text=text[:20]), self.assertRaises(strict_json.StrictJSONError):
                strict_json.loads(text)
        with self.assertRaises(strict_json.StrictJSONError):
            strict_json.dumps({"key": "\ud800"})
        self.assertEqual(strict_json.loads('"\\ud83d\\ude00"'), "\U0001f600")


class WorkspaceGuardPathTests(TestCase):
    def test_symlinked_guard_is_rejected_instead_of_followed(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q")
            guard = host.REGISTRY.workspace_guard_path(repo)
            guard.parent.mkdir(parents=True, exist_ok=True)
            outside = Path(tmp) / "outside.json"
            guard.symlink_to(outside)
            self.assertEqual(host.REGISTRY.workspace_guard_path(repo), guard)
            self.assertEqual(host.REGISTRY.workspace_guard_lock_path(repo).parent, guard.parent)
            with self.assertRaisesRegex(SystemExit, "must not be a symlink"):
                host.REGISTRY.read_workspace_guards(repo)
            self.assertFalse(outside.exists())

    def test_git_probes_ignore_inherited_git_variables(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            repo, other = Path(tmp) / "repo", Path(tmp) / "other"
            for path in (repo, other):
                path.mkdir()
                _git(path, "init", "-q")
            with mock.patch.dict(os.environ, {"GIT_DIR": str(other / ".git")}):
                guard = host.REGISTRY.workspace_guard_path(repo)
            self.assertEqual(guard.parent, (repo / ".git" / "agent-collab").resolve())


class PeerSupervisionSignalTests(TestCase):
    def test_signal_during_spawn_does_not_release_the_provider_gate(self):
        import signal as signal_module

        peer = fixtures.load_peer()
        real_popen = peer.subprocess.Popen
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "provider-started"

            def signalled_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                os.kill(os.getpid(), signal_module.SIGTERM)
                return process

            with mock.patch.object(peer.subprocess, "Popen", side_effect=signalled_popen):
                with self.assertRaises(SystemExit) as raised:
                    peer.run_peer_command(
                        [sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).write_text('x')"],
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                    )
            self.assertEqual(raised.exception.code, 128 + signal_module.SIGTERM)
            self.assertFalse(marker.exists())

    def test_signal_during_cleanup_is_deferred_until_cleanup_finishes(self):
        import signal as signal_module

        peer = fixtures.load_peer()
        real_cleanup = peer._cleanup_provider_process
        calls = []

        def interrupted_cleanup(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                os.kill(os.getpid(), signal_module.SIGTERM)
            return real_cleanup(*args, **kwargs)

        with mock.patch.object(peer, "_cleanup_provider_process", side_effect=interrupted_cleanup):
            with self.assertRaises(SystemExit) as raised:
                peer.run_peer_command(
                    [sys.executable, "-c", "print('done')"],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
                )
        self.assertEqual(raised.exception.code, 128 + signal_module.SIGTERM)
        self.assertEqual(len(calls), 1)


class SharedHistoryCleanupTests(TestCase):
    def run_clear(self, host, repo, run_root):
        args = host.build_parser().parse_args([
            "clear-history", "--all", "--yes", "--run-root", str(run_root), "--repo-root", str(repo),
        ])
        return host.ADMIN.build_clear_history_output(args)

    def test_another_worktrees_guarded_run_and_young_orphans_survive(self):
        host = fixtures.load_host()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_a, repo_b, run_root = base / "a", base / "b", base / "shared-runs"
            for path in (repo_a, repo_b):
                path.mkdir()
            owned = run_root / "run-a"
            young = run_root / "starting-orphan"
            old = run_root / "stale-orphan"
            for run_dir in (owned, young, old):
                run_dir.mkdir(parents=True)
                (run_dir / "host-request.json").write_text("{}")
            (owned / "peer-process.json").write_text(json.dumps({"repo_root": str(repo_a)}))
            os.utime(old, (1, 1))
            guards = {repo_a.resolve(): base / "guard-a.json", repo_b.resolve(): base / "guard-b.json"}
            with mock.patch.object(host.REGISTRY, "workspace_guard_path", side_effect=lambda root: guards[root.resolve()]):
                host.REGISTRY.acquire_workspace_guard(repo_a, "run-a", "codex", owned)
                status, output = self.run_clear(host, repo_b, run_root)
            self.assertEqual(status, 0)
            self.assertEqual([item["run_id"] for item in output["deleted"]], ["stale-orphan"])
            self.assertTrue(owned.exists())
            self.assertTrue(young.exists())
            self.assertFalse(old.exists())


class WrapperBaselineTests(TestCase):
    def test_wrapper_reuses_only_a_matching_host_baseline(self):
        peer = fixtures.load_peer()
        with tempfile.TemporaryDirectory() as tmp:
            repo, run_dir = Path(tmp) / "repo", Path(tmp) / "run"
            repo.mkdir()
            run_dir.mkdir()
            env = {"AGENT_COLLAB_RUN_DIR": str(run_dir)}
            self.assertIsNone(peer.host_baseline_snapshot(env, repo))
            baseline = {"mode": "git", "repo_root": str(repo.resolve()), "digest": "d"}
            (run_dir / "before.snapshot.json").write_text(json.dumps(baseline))
            self.assertEqual(peer.host_baseline_snapshot(env, repo), baseline)
            self.assertIsNone(peer.host_baseline_snapshot(env, Path(tmp)))
            (run_dir / "before.snapshot.json").write_text(json.dumps({**baseline, "mode": "isolated"}))
            self.assertIsNone(peer.host_baseline_snapshot(env, repo))
            self.assertIsNone(peer.host_baseline_snapshot({}, repo))


class IsolationDependencyScriptTests(TestCase):
    SCRIPT = fixtures.REPO_ROOT / "scripts" / "install-linux-isolation-deps.sh"

    def run_script(self, present, *args):
        if sys.platform != "linux" or not Path("/etc/os-release").exists():
            self.skipTest("the installer only runs on Linux distributions with os-release")
        with tempfile.TemporaryDirectory() as tmp:
            stubs = Path(tmp) / "bin"
            stubs.mkdir()
            log = Path(tmp) / "calls.log"
            for name in ("uname", "dirname", "cat"):
                real = subprocess.run(["sh", "-c", f"command -v {name}"], capture_output=True, text=True).stdout.strip()
                if real:
                    (stubs / name).symlink_to(real)
            for name in present:
                (stubs / name).write_text("#!/bin/sh\nexit 0\n")
            for name in ("sudo", "apt-get", "dnf"):
                (stubs / name).write_text(f"#!/bin/sh\necho {name} \"$@\" >> {log}\n" + ('exec "$@"\n' if name == "sudo" else ""))
            for item in stubs.iterdir():
                if not item.is_symlink():
                    item.chmod(0o755)
            completed = subprocess.run(
                ["/bin/bash", str(self.SCRIPT), *args], capture_output=True, text=True,
                env={"PATH": str(stubs), "HOME": tmp},
            )
            return completed, (log.read_text() if log.exists() else "")

    def test_check_reports_missing_commands_without_installing(self):
        completed, calls = self.run_script(["bwrap"], "--check")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("fuse-overlayfs: missing", completed.stderr)
        self.assertEqual(calls, "")

    def test_install_requests_only_missing_packages(self):
        completed, calls = self.run_script(["bwrap", "fusermount"], "--install")
        install_lines = [line for line in calls.splitlines() if " install " in f" {line} "]
        self.assertTrue(install_lines, completed.stderr + calls)
        self.assertTrue(all(line.endswith("fuse-overlayfs") for line in install_lines), install_lines)
        self.assertNotIn("fuse3", calls)
        self.assertNotIn("bubblewrap", calls)


class NormalizationSourceTests(TestCase):
    def test_missing_normalization_is_recorded_as_none_in_state(self):
        host = fixtures.load_host()
        state = fixtures.load_state()
        self.assertIn("none", state.NORMALIZATION_SOURCES)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "agent-collab-test"
            run_dir.mkdir()
            (run_dir / "host-request.json").write_text(json.dumps(fixtures.request()))
            (run_dir / "peer-process.json").write_text(json.dumps(
                fixtures.valid_peer_process(run_dir, Path(tmp), Path(tmp) / "guard.json")
            ))
            with mock.patch.object(host.PROCESS_TREE, "process_identity_matches", return_value=False):
                summary = host.RUNS.summarize_run(run_dir)
        self.assertEqual(summary["normalization_source"], "none")


class BsdProcessGroupTests(TestCase):
    def test_zombie_only_group_is_quiescent_despite_eperm(self):
        # macOS answers EPERM to killpg while a group holds an unreaped
        # zombie; the ps scan must still prove the group quiescent.
        safety = fixtures.load_peer().SAFETY
        child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        try:
            deadline = time.monotonic() + 10
            while fixtures.process_state(child.pid) not in {"Z", None} and time.monotonic() < deadline:
                time.sleep(0.05)
            with mock.patch.object(safety.sys, "platform", "darwin"):
                self.assertFalse(safety.process_group_alive(child.pid))
                with mock.patch.object(safety.os, "killpg", side_effect=PermissionError(1, "Operation not permitted")):
                    result = safety.terminate_process_group(child.pid)
            self.assertTrue(result["quiescent"], result)
        finally:
            child.wait()

    def test_signals_delivered_with_eperm_still_end_the_group(self):
        safety = fixtures.load_peer().SAFETY
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        real_killpg = os.killpg

        def deliver_then_eperm(pgid, signum):
            real_killpg(pgid, signum)
            raise PermissionError(1, "Operation not permitted")

        try:
            with mock.patch.object(safety.sys, "platform", "darwin"):
                self.assertTrue(safety.process_group_alive(child.pid))
                with mock.patch.object(safety.os, "killpg", side_effect=deliver_then_eperm):
                    result = safety.terminate_process_group(child.pid)
            self.assertTrue(result["quiescent"], result)
            self.assertEqual(result["outcome"], "terminated")
        finally:
            child.kill()
            child.wait()
