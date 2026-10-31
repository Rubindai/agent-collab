import filecmp
import hashlib
import json
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase

from agent_collab_test_support import (
    CANONICAL_PEER_SCHEMA,
    CLAUDE_PLUGIN_ROOT,
    CLAUDE_SKILL_ROOT,
    CODEX_PLUGIN_ROOT,
    CODEX_SKILL_ROOT,
    REPO_ROOT,
    RUNTIME_ROOT,
    availability_attestation,
    complete_settings,
    load_host,
    load_module,
    load_peer,
    request,
    setUpModule,
    tearDownModule,
    valid_host_synthesis,
    valid_provider_process,
    valid_report,
    valid_state_job,
)


class PackageTests(TestCase):
    def test_only_current_marketplace_package_layout_exists(self):
        self.assertTrue(CODEX_PLUGIN_ROOT.is_dir())
        self.assertTrue(CLAUDE_PLUGIN_ROOT.is_dir())
        self.assertEqual(CODEX_PLUGIN_ROOT, CLAUDE_PLUGIN_ROOT)
        self.assertFalse((REPO_ROOT / "plugins" / "claude").exists())
        for removed in ("codex-plugin", "claude-plugin", "codex-skill"):
            self.assertFalse((REPO_ROOT / removed).exists())
        for removed in (
            "install-codex-plugin.sh", "install-codex-skill.sh",
            "install-claude-plugin.sh", "sync-codex-skill.sh",
        ):
            self.assertFalse((REPO_ROOT / "scripts" / removed).exists())

    def test_release_versions_match(self):
        version = (REPO_ROOT / "VERSION").read_text().strip()
        self.assertRegex(version, r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
        self.assertIn(f"## {version} ", (REPO_ROOT / "CHANGELOG.md").read_text())
        for manifest in (
            CODEX_PLUGIN_ROOT / "plugin.json",
            CLAUDE_PLUGIN_ROOT / ".claude-plugin" / "plugin.json",
        ):
            self.assertEqual(json.loads(manifest.read_text())["version"], version)
        self.assertIn(f"## {version}", (REPO_ROOT / "CHANGELOG.md").read_text())

    def test_marketplaces_are_native_and_do_not_duplicate_version(self):
        codex = json.loads((REPO_ROOT / ".agents/plugins/marketplace.json").read_text())
        centry = codex["plugins"][0]
        self.assertEqual(centry["source"], {"source": "local", "path": "./plugins/agent-collab"})
        self.assertEqual(centry["policy"], {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
        self.assertEqual(centry["category"], "Productivity")
        self.assertNotIn("version", centry)
        claude = json.loads((REPO_ROOT / ".claude-plugin/marketplace.json").read_text())
        aentry = claude["plugins"][0]
        self.assertEqual(aentry["source"], "./plugins/agent-collab")
        self.assertNotIn("version", aentry)

    def test_packages_match_canonical_runtime_and_sync_check(self):
        self.assertEqual(CODEX_SKILL_ROOT, CLAUDE_SKILL_ROOT)
        for dirname in ("scripts", "references", "schemas"):
            comparison = filecmp.dircmp(RUNTIME_ROOT / dirname, CODEX_SKILL_ROOT / dirname)
            self.assertEqual(comparison.left_only, [])
            self.assertEqual(comparison.right_only, [])
            self.assertEqual(comparison.diff_files, [])
        claude_agents = filecmp.dircmp(
            RUNTIME_ROOT / "agents" / "claude",
            CODEX_PLUGIN_ROOT / "agents",
        )
        self.assertEqual(claude_agents.left_only, [])
        self.assertEqual(claude_agents.right_only, [])
        self.assertEqual(claude_agents.diff_files, [])
        self.assertTrue(filecmp.cmp(
            RUNTIME_ROOT / "agents" / "codex" / "openai.yaml",
            CODEX_SKILL_ROOT / "agents" / "openai.yaml",
            shallow=False,
        ))
        completed = subprocess.run(
            [str(REPO_ROOT / "scripts/sync-packages.sh"), "--check"],
            cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_sync_rejects_and_removes_unexpected_packaged_payload(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)
            for relative in (".agents", ".claude-plugin"):
                shutil.copytree(REPO_ROOT / relative, root / relative)
            shutil.copytree(RUNTIME_ROOT, root / "tools" / "agent-collab")
            shutil.copytree(CODEX_PLUGIN_ROOT, root / "plugins" / "agent-collab")
            (root / "scripts").mkdir()
            shutil.copy2(REPO_ROOT / "scripts" / "sync-packages.sh", root / "scripts")
            shutil.copy2(REPO_ROOT / "VERSION", root)
            shutil.copy2(REPO_ROOT / "LICENSE", root)
            stale = root / "plugins" / "agent-collab" / "skills" / "agent-collab" / "legacy.txt"
            stale.write_text("legacy compatibility payload", encoding="utf-8")

            check = subprocess.run(
                [str(root / "scripts" / "sync-packages.sh"), "--check"],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(check.returncode, 0)
            self.assertIn("unexpected packaged skill entry", check.stderr)

            sync = subprocess.run(
                [str(root / "scripts" / "sync-packages.sh")],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)
            self.assertFalse(stale.exists())

    def test_skill_frontmatter_links_and_instruction_budget(self):
        text = (CODEX_SKILL_ROOT / "SKILL.md").read_text()
        frontmatter = text.split("---", 2)[1]
        self.assertEqual([line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line],
                         ["name", "description"])
        self.assertLessEqual(len(text.encode("utf-8")), 8000)
        for reference in re.findall(r"\]\((references/[^)]+)\)", text):
            self.assertTrue((CODEX_SKILL_ROOT / reference).is_file(), reference)
        self.assertEqual((REPO_ROOT / "CLAUDE.md").read_text(), "@AGENTS.md\n")

    def test_helper_frontmatter_inherits_without_tool_restrictions(self):
        for path in (CLAUDE_PLUGIN_ROOT / "agents").glob("*.md"):
            frontmatter = path.read_text().split("---", 2)[1]
            fields = dict(line.split(":", 1) for line in frontmatter.splitlines() if ":" in line)
            self.assertEqual(fields["model"].strip(), "inherit", path.name)
            self.assertTrue({"effort", "tools", "disallowedTools"}.isdisjoint(fields), path.name)

    def test_settings_audit_matches_runtime_schema_defaults_and_environment(self):
        host = load_host()
        readme = (REPO_ROOT / "README.md").read_text()
        audit = readme.split("### Persisted Agent Collab settings", 1)[1].split(
            "### Per-run-only controls", 1
        )[0]
        rows = re.findall(r"^\| `([^`]+)` \| `([^`]*)` \|", audit, flags=re.MULTILINE)
        documented_defaults = dict(rows)
        workspace_row = re.search(
            r"^\| `workspace_mode` \| Linux: `([^`]*)`; macOS and Windows: `([^`]*)` \|",
            audit,
            flags=re.MULTILINE,
        )
        self.assertIsNotNone(workspace_row)
        self.assertEqual(workspace_row.groups(), ("isolated", "direct"))
        documented_defaults["workspace_mode"] = (
            workspace_row.group(2) if host.sys.platform in {"darwin", "win32"} else workspace_row.group(1)
        )
        expected_defaults = {
            key: (
                "true" if value is True
                else "false" if value is False
                else json.dumps(value, separators=(",", ":")) if isinstance(value, list)
                else str(value)
            )
            for key, value in host.CONFIG.SETTING_DEFAULTS.items()
        }
        self.assertEqual(documented_defaults, expected_defaults)

        schema = json.loads((RUNTIME_ROOT / "schemas/settings.schema.json").read_text())
        settings_schema = schema["properties"]["settings"]
        self.assertEqual(set(settings_schema["required"]), set(host.CONFIG.SETTING_DEFAULTS))
        self.assertEqual(set(settings_schema["properties"]), set(host.CONFIG.SETTING_DEFAULTS))
        self.assertEqual(set(host.CONFIG.SETTING_ENV_KEYS), set(host.CONFIG.SETTING_DEFAULTS))

        env_section = readme.split("Environment keys:", 1)[1].split("Run `doctor`", 1)[0]
        documented_env = set(re.findall(r"`([A-Z][A-Z0-9_]+)`", env_section))
        self.assertEqual(documented_env, set(host.CONFIG.SETTING_ENV_KEYS.values()))

    def test_json_schemas_validate_representatives(self):
        strict_json = load_module(
            RUNTIME_ROOT / "scripts" / "strict_json.py",
            "agent_collab_release_strict_json_test",
        )
        for path in [path for path in RUNTIME_ROOT.rglob("*.json") if "runs" not in path.parts] + [
            REPO_ROOT / ".agents/plugins/marketplace.json",
            REPO_ROOT / ".claude-plugin/marketplace.json",
            CODEX_PLUGIN_ROOT / "plugin.json",
            CLAUDE_PLUGIN_ROOT / ".claude-plugin/plugin.json",
        ]:
            strict_json.load(path)
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        jsonschema.validate(request(), json.loads((RUNTIME_ROOT / "schemas/host-request.schema.json").read_text()))
        host_request_schema = json.loads((RUNTIME_ROOT / "schemas/host-request.schema.json").read_text())
        peer_runtime = load_peer()
        for invalid_request in (
            request(origin="codex", host="codex", peer="codex"),
            request(run_id="."),
            request(target="   "),
            request(codex_config=["model=other"]),
            request(claude_tools="default,Bash"),
        ):
            with self.subTest(invalid_request=invalid_request), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(invalid_request, host_request_schema)
            with self.assertRaises(peer_runtime.RequestValidationError):
                peer_runtime.validate_request(invalid_request)
        jsonschema.validate(
            availability_attestation("claude", "claude-opus-5-5", "xhigh"),
            json.loads((RUNTIME_ROOT / "schemas/availability-attestation.schema.json").read_text()),
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                request(peer_effort=""),
                json.loads((RUNTIME_ROOT / "schemas/host-request.schema.json").read_text()),
            )
        peer_schema = json.loads(CANONICAL_PEER_SCHEMA.read_text())
        jsonschema.validate(valid_report(), peer_schema)
        peer = load_peer()
        for invalid_report in (
            valid_report(error={"kind": "x", "message": "x", "details": None}),
            valid_report(status="peer_failed", verdict="blocked", error=None),
        ):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(invalid_report, peer_schema)
            with self.assertRaises(peer.PeerReportValidationError):
                peer.validate_peer_report(invalid_report)
        jsonschema.validate(
            {
                "schema_version": "3.0",
                "source": "direct_json",
                "artifact_source": "peer_report",
                "input_bytes": 10,
                "warnings": [],
                "validation_status": "ok",
            },
            json.loads((RUNTIME_ROOT / "schemas/peer-normalization.schema.json").read_text()),
        )
        jsonschema.validate(
            valid_provider_process(),
            json.loads((RUNTIME_ROOT / "schemas/provider-process.schema.json").read_text()),
        )
        host = load_host()
        payload = {"schema_version": "2.0", "updated_at": "2026-07-18T00:00:00Z", "settings": complete_settings(host)}
        settings_schema = json.loads((RUNTIME_ROOT / "schemas/settings.schema.json").read_text())
        jsonschema.validate(payload, settings_schema)
        fableton = {**payload, "settings": complete_settings(host, claude_model="Fableton")}
        jsonschema.validate(fableton, settings_schema)
        host.CONFIG.validate_v2_settings_types(fableton["settings"], "test settings")
        for invalid_settings in (
            complete_settings(host, agent_timeout_seconds="999999"),
            complete_settings(host, claude_model="claude-fable-5"),
            complete_settings(host, codex_model="   "),
        ):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate({**payload, "settings": invalid_settings}, settings_schema)
            with self.assertRaises(ValueError):
                host.CONFIG.validate_v2_settings_types(invalid_settings, "test settings")
        provider_schema = json.loads(
            (RUNTIME_ROOT / "schemas/provider-process.schema.json").read_text()
        )
        invalid_cleanup = valid_provider_process(
            status="cleanup_failed",
            pid=None,
            pgid=None,
            process_identity=None,
            cleanup_outcome=None,
            completed_at=None,
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid_cleanup, provider_schema)
        with self.assertRaises(host.ARTIFACTS.ArtifactValidationError):
            host.ARTIFACTS.validate_provider_process(invalid_cleanup, request())
        jsonschema.validate(
            {"schema_version": "3.0", "jobs": [valid_state_job()]},
            json.loads((RUNTIME_ROOT / "schemas/state.schema.json").read_text()),
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {"schema_version": "3.0", "jobs": [{**valid_state_job(), "legacy": True}]},
                json.loads((RUNTIME_ROOT / "schemas/state.schema.json").read_text()),
            )
        jsonschema.validate(
            valid_host_synthesis(),
            json.loads((RUNTIME_ROOT / "schemas/host-synthesis.schema.json").read_text()),
        )
        jsonschema.validate(
            {
                "schema_version": "3.0", "run_id": request()["run_id"],
                "reports": [{"name": "reviewer", "summary": "ok", "claims": []}],
            },
            json.loads((RUNTIME_ROOT / "schemas/helper-reports.schema.json").read_text()),
        )
        jsonschema.validate(
            {
                "schema_version": "3.0",
                "run_id": request()["run_id"],
                "status": "cancelled",
                "outcome": "cancelled_quiescent",
                "reason": "peer_not_running",
                "elapsed_seconds": 61,
                "peer_was_alive": False,
                "process_cleanup": {
                    "quiescent": True,
                    "provider": {"outcome": "already_quiescent", "quiescent": True},
                    "wrapper": {"outcome": "not_running", "quiescent": True, "pgid": 43210},
                },
                "preserved_artifacts": {
                    "peer_report": None,
                    "peer_raw": None,
                    "peer_normalization": None,
                    "host_result": None,
                    "host_synthesis": None,
                    "claim_matrix": None,
                },
                "workspace_mutation": None,
                "workspace_mutation_sha256": hashlib.sha256(b"null").hexdigest(),
                "completed_at": "2026-07-24T00:00:00Z",
            },
            json.loads((RUNTIME_ROOT / "schemas/cancel-result.schema.json").read_text()),
        )

    def test_maintenance_files_and_changelog_policy(self):
        self.assertTrue((REPO_ROOT / "scripts/sync-packages.sh").stat().st_mode & stat.S_IXUSR)
        self.assertFalse(any(CODEX_SKILL_ROOT.glob("CHANGELOG*")))
        self.assertFalse(any(CLAUDE_SKILL_ROOT.glob("CHANGELOG*")))

