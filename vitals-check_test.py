import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def load_module(path: Path):
    spec = importlib.util.spec_from_file_location("vitals_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class VitalsCheckTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime.now(ZoneInfo("Europe/Rome"))
        self._build_fixture()
        self.script = Path(__file__).with_name("vitals-check.py")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _dt(self, days=0):
        return (self.now + timedelta(days=days)).isoformat()

    def _build_items(self):
        return [
            {
                "id": 1,
                "fact": "Stable fact",
                "category": "status",
                "timestamp": self._dt(-2),
                "source": "test",
                "status": "active",
                "lastAccessed": self._dt(-1),
                "accessCount": 3,
                "relatedEntities": ["projects/demo"],
            },
            {
                "id": 2,
                "fact": "Superseded item",
                "category": "decision",
                "timestamp": self._dt(-10),
                "source": "test",
                "status": "superseded",
                "supersededBy": 3,
                "lastAccessed": self._dt(-10),
                "accessCount": 2,
                "relatedEntities": ["projects/demo"],
            },
            {
                "id": 3,
                "fact": "Replacement item",
                "category": "decision",
                "timestamp": self._dt(-5),
                "source": "test",
                "status": "active",
                "lastAccessed": self._dt(-2),
                "accessCount": 2,
                "relatedEntities": ["projects/demo"],
            },
        ]

    def _build_fixture(self):
        def _on_rm_error(func, path, exc_info):
            try:
                os.chmod(path, 0o700)
                func(path)
            except Exception:
                pass

        for name in ["workspace", "workspace-telatix", ".openclaw", "bin", ".bun", "vitals-cron-state.json", "vitals-checksums.json"]:
            target = self.root / name
            if target.is_dir():
                shutil.rmtree(target, onexc=_on_rm_error)
            elif target.exists():
                target.unlink()
        ws = self.root / "workspace"
        pr = ws / "praeco"
        tws = self.root / "workspace-telatix"
        oc = self.root / ".openclaw"
        md = ws / "memory"
        pmd = pr / "memory"
        (ws / ".git").mkdir(parents=True, exist_ok=True)
        (ws / "skills").mkdir(parents=True, exist_ok=True)
        (tws / "skills").mkdir(parents=True, exist_ok=True)
        (oc / "skills").mkdir(parents=True, exist_ok=True)
        (oc / "sessions").mkdir(parents=True, exist_ok=True)
        (ws / "memory" / "projects" / "demo").mkdir(parents=True, exist_ok=True)
        (ws / "memory" / "areas" / "people" / "alice").mkdir(parents=True, exist_ok=True)
        (ws / "memory" / "areas" / "companies" / "acme").mkdir(parents=True, exist_ok=True)
        (pr / "memory" / "strategy").mkdir(parents=True, exist_ok=True)
        (pr / "memory").mkdir(parents=True, exist_ok=True)

        items = self._build_items()
        self._write_json(ws / "memory" / "projects" / "demo" / "items.json", items)
        self._write_json(ws / "memory" / "areas" / "people" / "alice" / "items.json", self._build_items())
        self._write_json(ws / "memory" / "areas" / "companies" / "acme" / "items.json", self._build_items())

        regen = self.now.date().isoformat()
        for p in [
            ws / "memory" / "projects" / "demo" / "summary.md",
            ws / "memory" / "areas" / "people" / "alice" / "summary.md",
            ws / "memory" / "areas" / "companies" / "acme" / "summary.md",
        ]:
            p.write_text(
                "# Summary\nStable fact\nReplacement item\nLast regenerated: %s\n" % regen,
                encoding="utf-8",
            )

        for i in range(7):
            day = (self.now.date() - timedelta(days=i)).isoformat()
            note = "# Daily\n"
            if i == 0:
                note += "## Praeco Cross-Pollination\nnone\nTask A\n"
            (md / f"{day}.md").write_text(note, encoding="utf-8")
            (pmd / f"{day}.md").write_text("# Praeco\n", encoding="utf-8")
        (ws / "MEMORY.md").write_text("Behavioral preferences and communication style only.\n", encoding="utf-8")
        (ws / "AGENTS.md").write_text(
            "agent:main:telegram:group:-100EXAMPLE:topic:1\n"
            "agent:praeco:telegram:group:-100EXAMPLE:topic:588\n"
            "agent:telatix:telegram:group:-100EXAMPLE:topic:1953\n",
            encoding="utf-8",
        )
        (ws / "AGENTS.md").write_text(
            (ws / "AGENTS.md").read_text(encoding="utf-8")
            + "praeco-dream-cycle: 15 2 * * *\n"
            + "dream-cycle: 0 3 * * *\n",
            encoding="utf-8",
        )

        self._write_json(ws / "tasks.json", [{"name": "Task A", "status": "pending", "description": "Desc", "deadline": self._dt(2)}])
        self._write_json(oc / "openclaw.json", {"models": ["gpt-4.1"]})
        (oc / "openclaw.json.backup.latest").write_text("{}", encoding="utf-8")

        (pr / "BRAND_VOICE.md").write_text("voice", encoding="utf-8")
        (pr / "memory" / "strategy" / "content-lessons.md").write_text("lessons", encoding="utf-8")
        (pr / f"content-sequence-{self.now.strftime('%Y-%m')}.md").write_text("ok", encoding="utf-8")

        skill_md = "---\nname: demo-skill\ndescription: This is a valid description that is definitely longer than fifty characters for validation.\n---\n"
        (ws / "skills" / "demo").mkdir(parents=True, exist_ok=True)
        (ws / "skills" / "demo" / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (ws / "skills" / "demo" / "scripts").mkdir(parents=True, exist_ok=True)
        (ws / "skills" / "demo" / "scripts" / "a.py").write_text("x=1\n", encoding="utf-8")
        (oc / "skills" / "ui-ux-pro-max").mkdir(parents=True, exist_ok=True)
        (oc / "skills" / "ui-ux-pro-max" / "SKILL.md").write_text(skill_md.replace("demo-skill", "ui-ux-pro-max"), encoding="utf-8")
        (oc / "skills" / "ui-ux-pro-max" / "data").mkdir(parents=True, exist_ok=True)
        (oc / "skills" / "ui-ux-pro-max" / "data" / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        cron = {
            "generatedAtMs": int(self.now.timestamp() * 1000),
            "crons": [
                {
                    "name": "workspace-backup",
                    "enabled": True,
                    "schedule": "*/30 * * * *",
                    "consecutiveErrors": 0,
                    "lastError": "",
                    "tz": "Europe/Rome",
                    "lastRunAtMs": int((self.now - timedelta(minutes=30)).timestamp() * 1000),
                    "delivery": {"channelEnabled": True},
                    "kind": "cron",
                    "model": "gpt-4.1",
                },
                {
                    "name": "praeco-dream-cycle",
                    "enabled": True,
                    "schedule": "15 2 * * *",
                    "consecutiveErrors": 0,
                    "lastError": "",
                    "tz": "Europe/Rome",
                    "lastRunAtMs": int((self.now - timedelta(hours=24)).timestamp() * 1000),
                    "delivery": {"channelEnabled": True},
                    "kind": "cron",
                    "model": "gpt-4.1",
                },
                {
                    "name": "dream-cycle",
                    "enabled": True,
                    "schedule": "0 3 * * *",
                    "consecutiveErrors": 0,
                    "lastError": "",
                    "tz": "Europe/Rome",
                    "lastRunAtMs": int((self.now - timedelta(hours=24)).timestamp() * 1000),
                    "delivery": {"channelEnabled": True},
                    "kind": "cron",
                    "model": "gpt-4.1",
                },
            ],
        }
        extra_crons = [
            ("morning-brief", "0 6 * * *"),
            ("weekly-summary-regen", "0 7 * * 0"),
            ("praeco-weekly-summary", "30 7 * * 0"),
            ("session-cleanup", "0 */6 * * *"),
            ("update-check", "15 */4 * * *"),
            ("security-audit", "45 */8 * * *"),
            ("inbox-monitor", "*/10 * * * *"),
            ("daily-ai-study-reminder", "30 8 * * *"),
            ("daily-vitals-check", "30 6 * * *"),
        ]
        for name, schedule in extra_crons:
            last_run = int((self.now - timedelta(hours=12)).timestamp() * 1000)
            if name == "inbox-monitor":
                last_run = int((self.now - timedelta(minutes=5)).timestamp() * 1000)
            cron["crons"].append(
                {
                    "name": name,
                    "enabled": True,
                    "schedule": schedule,
                    "consecutiveErrors": 0,
                    "lastError": "",
                    "tz": "Europe/Rome",
                    "lastRunAtMs": last_run,
                    "delivery": {"channelEnabled": True},
                    "kind": "cron",
                    "model": "gpt-4.1",
                }
            )
        tools_lines = ["## Crons"] + [c["name"] for c in cron["crons"]] + ["## Models", "gpt-4.1"]
        (ws / "TOOLS.md").write_text("\n".join(tools_lines) + "\n", encoding="utf-8")

        self._write_json(self.root / "vitals-cron-state.json", cron)

        for bindir in [self.root / "bin", self.root / ".bun" / "bin"]:
            bindir.mkdir(parents=True, exist_ok=True)
        qmd = self.root / ".bun" / "bin" / "qmd"
        qmd.write_text("#!/bin/sh\nif [ \"$1\" = \"status\" ]; then\n  date -u +\"updated_at=%Y-%m-%dT%H:%M:%SZ\"\nelse\n  echo qmd\nfi\n", encoding="utf-8")
        qmd.chmod(0o755)

        for name, script in {
            "gh": "#!/bin/sh\nexit 0\n",
            "gog": "#!/bin/sh\nexit 0\n",
            "vercel": "#!/bin/sh\nexit 0\n",
            "node": "#!/bin/sh\necho v22.3.0\n",
            "git": "#!/bin/sh\nif [ \"$1\" = \"status\" ]; then echo \"\"; exit 0; fi\nif [ \"$1\" = \"rev-list\" ]; then echo 20; exit 0; fi\nexit 0\n",
            "openclaw": "#!/bin/sh\necho '{\"models\":[\"gpt-4.1\"]}'\n",
        }.items():
            p = self.root / "bin" / name
            p.write_text(script, encoding="utf-8")
            p.chmod(0o755)

        os.chmod(oc, 0o700)
        os.chmod(oc / "openclaw.json", 0o600)

        self.env = {
            "WORKSPACE": str(ws),
            "PRAECO_WORKSPACE": str(pr),
            "TELATIX_WORKSPACE": str(tws),
            "OPENCLAW_DIR": str(oc),
            "CONFIG_PATH": str(oc / "openclaw.json"),
            "QMD_BINARY": str(qmd),
            "SKILLS_GLOBAL": str(oc / "skills"),
            "SKILLS_LUMEN": str(ws / "skills"),
            "SKILLS_TELATIX": str(tws / "skills"),
            "MEMORY_DIR": str(md),
            "AUDITS_DIR": str(md / "audits"),
            "CRON_STATE_PATH": str(self.root / "vitals-cron-state.json"),
            "CHECKSUM_PATH": str(self.root / "vitals-checksums.json"),
            "PATH": f"{self.root / 'bin'}:{os.environ.get('PATH','')}",
        }

    def _run(self, extra_env=None):
        env = os.environ.copy()
        env.update(self.env)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["python3", str(self.script)],
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )

    def _make_sparse_file(self, path: Path, size: int):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            f.truncate(size)

    def _prime_checksum_then_change_brand_voice(self):
        self._run()
        (Path(self.env["PRAECO_WORKSPACE"]) / "BRAND_VOICE.md").write_text("changed", encoding="utf-8")

    def _break_config_json(self):
        Path(self.env["CONFIG_PATH"]).write_text("{", encoding="utf-8")
        oc = self.root / "bin" / "openclaw"
        oc.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        os.chmod(oc, 0o755)

    def test_suite_fails_before_implementation(self):
        if self.script.exists():
            self.skipTest("implementation exists")
        proc = self._run()
        self.assertNotEqual(proc.returncode, 0)

    def test_json_output_schema_and_exit_codes(self):
        if not self.script.exists():
            self.skipTest("implementation missing")
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertIn("timestamp", data)
        self.assertIn("duration_seconds", data)
        self.assertIn("summary", data)
        self.assertIn("categories", data)
        self.assertIn("stats", data)
        self.assertIn("discovery", data)
        self.assertGreaterEqual(data["summary"]["total_checks"], 73)

    def test_handles_missing_files_gracefully(self):
        if not self.script.exists():
            self.skipTest("implementation missing")
        Path(self.env["CRON_STATE_PATH"]).unlink(missing_ok=True)
        Path(self.env["CONFIG_PATH"]).unlink(missing_ok=True)
        proc = self._run()
        self.assertIn(proc.returncode, (0, 1, 2), proc.stderr)
        data = json.loads(proc.stdout)
        self.assertGreaterEqual(data["summary"]["total_checks"], 73)

    def test_agent_auto_discovery_from_config(self):
        if not self.script.exists():
            self.skipTest("implementation missing")
        ws = Path(self.env["WORKSPACE"])
        praeco = Path(self.env["PRAECO_WORKSPACE"])
        telatix = Path(self.env["TELATIX_WORKSPACE"])
        extra = self.root / "workspace-ops"
        extra.mkdir(parents=True, exist_ok=True)
        self._write_json(
            Path(self.env["CONFIG_PATH"]),
            {
                "agents": {
                    "defaults": {"workspace": str(ws)},
                    "list": [
                        {"id": "main"},
                        {"id": "praeco", "workspace": str(praeco)},
                        {"id": "telatix", "workspace": str(telatix)},
                        {"id": "ops", "workspace": str(extra)},
                    ],
                }
            },
        )
        proc = self._run({"PRAECO_WORKSPACE": str(self.root / "wrong-praeco"), "TELATIX_WORKSPACE": str(self.root / "wrong-telatix")})
        data = json.loads(proc.stdout)
        discovery = data.get("discovery", {})
        self.assertIn("main", discovery.get("agents_found", []))
        self.assertIn("praeco", discovery.get("agents_found", []))
        self.assertIn("telatix", discovery.get("agents_found", []))
        self.assertIn("ops", discovery.get("agents_found", []))

    def test_discovery_fallback_when_config_unreadable(self):
        if not self.script.exists():
            self.skipTest("implementation missing")
        Path(self.env["CONFIG_PATH"]).write_text("{", encoding="utf-8")
        proc = self._run()
        data = json.loads(proc.stdout)
        self.assertIn("discovery", data)
        self.assertIn("config_source", data["discovery"])

    def test_external_timeout_handling(self):
        if not self.script.exists():
            self.skipTest("implementation missing")
        slow = self.root / "bin" / "vercel"
        slow.write_text("#!/bin/sh\nsleep 20\n", encoding="utf-8")
        slow.chmod(0o755)
        proc = self._run()
        self.assertIn(proc.returncode, (1, 2))
        data = json.loads(proc.stdout)
        checks = []
        for cat in data["categories"].values():
            checks.extend(cat["checks"])
        vercel = [c for c in checks if c["name"] == "vercel_auth"]
        self.assertTrue(vercel)
        self.assertIn("timeout", vercel[0]["details"].lower())

    def test_all_73_checks_have_good_and_bad_paths(self):
        if not self.script.exists():
            self.skipTest("implementation missing")
        good = json.loads(self._run().stdout)
        check_map = {}
        for category in good["categories"].values():
            for check in category["checks"]:
                check_map[check["name"]] = check["status"]
        self.assertGreaterEqual(len(check_map), 73)
        for name, status in check_map.items():
            self.assertEqual(status, "pass", f"expected pass baseline for {name}, got {status}")

        bad_mutations = {
            "json_valid": lambda: (Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json").write_text("{", encoding="utf-8"),
            "duplicate_ids": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", self._build_items() + [{**self._build_items()[0], "id": 1}]),
            "required_fields": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{"id": 1}]),
            "empty_facts": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "fact": ""}]),
            "category_valid": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "category": "bad"}]),
            "timestamp_valid": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "timestamp": self._dt(5)}]),
            "supersede_chain_valid": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "supersededBy": 999, "status": "active"}]),
            "orphaned_supersede": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[1], "supersededBy": 999}]),
            "access_count_sane": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "accessCount": 999}]),
            "duplicate_fact_text": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [self._build_items()[0], {**self._build_items()[0], "id": 4}]),
            "id_sequence": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "id": 1}, {**self._build_items()[1], "id": 20}]),
            "cold_fact_ratio": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "lastAccessed": self._dt(-40)}]),
            "stale_active_blockers": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "fact": "blocked issue", "lastAccessed": self._dt(-20)}]),
            "entity_completeness": lambda: (Path(self.env["MEMORY_DIR"]) / "areas" / "people" / "alice" / "summary.md").unlink(missing_ok=True),
            "summary_freshness": lambda: (Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "summary.md").write_text("Last regenerated: 2000-01-01\n", encoding="utf-8"),
            "summary_items_drift": lambda: (Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "summary.md").write_text("Unrelated\nLast regenerated: %s\n" % self.now.date().isoformat(), encoding="utf-8"),
            "relationship_islands": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "relatedEntities": []}]),
            "fact_velocity": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "timestamp": self._dt(-20)}]),
            "archive_candidates": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "lastAccessed": self._dt(-40)}]),
            "cross_references_valid": lambda: self._write_json(Path(self.env["MEMORY_DIR"]) / "projects" / "demo" / "items.json", [{**self._build_items()[0], "relatedEntities": ["projects/missing"]}]),
            "daily_note_gaps": lambda: (Path(self.env["MEMORY_DIR"]) / f"{self.now.date().isoformat()}.md").unlink(missing_ok=True),
            "praeco_daily_note_gaps": lambda: (Path(self.env["PRAECO_WORKSPACE"]) / "memory" / f"{self.now.date().isoformat()}.md").unlink(missing_ok=True),
            "cross_pollination_present": lambda: (Path(self.env["MEMORY_DIR"]) / f"{self.now.date().isoformat()}.md").write_text("No section\n", encoding="utf-8"),
            "memory_md_guard": lambda: (Path(self.env["WORKSPACE"]) / "MEMORY.md").write_text("projects/demo status", encoding="utf-8"),
            "daily_note_size": lambda: (Path(self.env["MEMORY_DIR"]) / f"{self.now.date().isoformat()}.md").write_text("x" * 25000, encoding="utf-8"),
            "tasks_json_valid": lambda: (Path(self.env["WORKSPACE"]) / "tasks.json").write_text("{", encoding="utf-8"),
            "stale_deadlines": lambda: self._write_json(Path(self.env["WORKSPACE"]) / "tasks.json", [{"name": "a", "status": "active", "description": "d", "deadline": self._dt(-1)}]),
            "missing_descriptions": lambda: self._write_json(Path(self.env["WORKSPACE"]) / "tasks.json", [{"name": "a", "status": "active", "description": ""}]),
            "zombie_tasks": lambda: self._write_json(Path(self.env["WORKSPACE"]) / "tasks.json", [{"name": "Zombie task", "status": "active", "description": "ghost", "deadline": self._dt(2)}]),
            "expected_crons_exist": lambda: self._write_json(Path(self.env["CRON_STATE_PATH"]), {"generatedAtMs": int(self.now.timestamp()*1000), "crons": []}),
            "all_enabled": lambda: self._tweak_cron("workspace-backup", {"enabled": False}),
            "consecutive_errors": lambda: self._tweak_cron("workspace-backup", {"consecutiveErrors": 2}),
            "timeout_errors": lambda: self._tweak_cron("workspace-backup", {"lastError": "timed out"}),
            "timezone_consistency": lambda: self._tweak_cron("workspace-backup", {"tz": ""}),
            "schedule_collision": lambda: self._tweak_cron("morning-brief", {"schedule": "*/30 * * * *"}),
            "dream_cycle_ordering": lambda: self._tweak_cron("dream-cycle", {"schedule": "0 2 * * *"}),
            "stale_runs": lambda: self._tweak_cron("workspace-backup", {"lastRunAtMs": int((self.now - timedelta(days=3)).timestamp() * 1000)}),
            "delivery_sanity": lambda: self._tweak_cron("workspace-backup", {"delivery": {"channelEnabled": False}}),
            "orphan_one_shots": lambda: self._append_cron({"name": "one", "enabled": True, "schedule": "* * * * *", "consecutiveErrors": 0, "lastError": "", "tz": "Europe/Rome", "lastRunAtMs": int(self.now.timestamp()*1000), "delivery": {"channelEnabled": True}, "kind": "at", "deleteAfterRun": False, "model": "gpt-4.1"}),
            "model_valid": lambda: self._tweak_cron("workspace-backup", {"model": "unknown-model"}),
            "skill_md_present": lambda: (Path(self.env["SKILLS_LUMEN"]) / "demo" / "SKILL.md").unlink(missing_ok=True),
            "frontmatter_valid": lambda: (Path(self.env["SKILLS_LUMEN"]) / "demo" / "SKILL.md").write_text("no frontmatter", encoding="utf-8"),
            "description_length": lambda: (Path(self.env["SKILLS_LUMEN"]) / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: short\n---\n", encoding="utf-8"),
            "symlink_integrity": lambda: os.symlink(str(Path(self.tmp.name)/"missing-target"), Path(self.env["SKILLS_LUMEN"]) / "demo" / "bad.link"),
            "script_exists": lambda: (Path(self.env["SKILLS_LUMEN"]) / "demo" / "scripts" / "a.py").write_text("def bad(:\n", encoding="utf-8"),
            "name_conflicts": lambda: (Path(self.env["SKILLS_TELATIX"]) / "dup").mkdir(parents=True, exist_ok=True) or (Path(self.env["SKILLS_TELATIX"]) / "dup" / "SKILL.md").write_text((Path(self.env["SKILLS_LUMEN"]) / "demo" / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8"),
            "data_files_exist": lambda: (Path(self.env["SKILLS_GLOBAL"]) / "ui-ux-pro-max" / "data" / "x.csv").write_text("", encoding="utf-8"),
            "workspace_exists": lambda: shutil.rmtree(Path(self.env["TELATIX_WORKSPACE"]), ignore_errors=True),
            "workspace_writable": lambda: os.chmod(Path(self.env["PRAECO_WORKSPACE"]), 0o500),
            "workspace_size": lambda: self._make_sparse_file(Path(self.env["WORKSPACE"]) / "big.tmp", 205 * 1024 * 1024),
            "praeco_brand_voice_checksum": lambda: (Path(self.env["PRAECO_WORKSPACE"]) / "BRAND_VOICE.md").unlink(missing_ok=True),
            "praeco_content_pipeline_current": lambda: (Path(self.env["PRAECO_WORKSPACE"]) / f"content-sequence-{self.now.strftime('%Y-%m')}.md").unlink(missing_ok=True),
            "agents_md_session_keys": lambda: (Path(self.env["WORKSPACE"]) / "AGENTS.md").write_text("bad", encoding="utf-8"),
            "disk_openclaw": lambda: self._make_sparse_file(Path(self.env["OPENCLAW_DIR"]) / "huge.bin", 1100 * 1024 * 1024),
            "disk_workspace": lambda: self._make_sparse_file(Path(self.env["WORKSPACE"]) / "huge2.bin", 510 * 1024 * 1024),
            "disk_sessions": lambda: self._make_sparse_file(Path(self.env["OPENCLAW_DIR"]) / "sessions" / "huge3.bin", 310 * 1024 * 1024),
            "permissions_openclaw_dir": lambda: os.chmod(Path(self.env["OPENCLAW_DIR"]), 0o755),
            "permissions_config": lambda: os.chmod(Path(self.env["CONFIG_PATH"]), 0o644),
            "git_clean": lambda: (self.root / "bin" / "git").write_text("#!/bin/sh\nif [ \"$1\" = \"status\" ]; then echo M; exit 0; fi\nif [ \"$1\" = \"rev-list\" ]; then echo 20; exit 0; fi\n", encoding="utf-8") or os.chmod(self.root / "bin" / "git", 0o755),
            "git_backup_frequency": lambda: (self.root / "bin" / "git").write_text("#!/bin/sh\nif [ \"$1\" = \"status\" ]; then echo \"\"; exit 0; fi\nif [ \"$1\" = \"rev-list\" ]; then echo 1; exit 0; fi\n", encoding="utf-8") or os.chmod(self.root / "bin" / "git", 0o755),
            "config_backup_fresh": lambda: os.utime(Path(self.env["OPENCLAW_DIR"]) / "openclaw.json.backup.latest", (0, 0)),
            "large_files": lambda: (Path(self.env["WORKSPACE"]) / "large.dat").write_bytes(b"x" * (6 * 1024 * 1024)),
            "node_version": lambda: (self.root / "bin" / "node").write_text("#!/bin/sh\necho v21.0.0\n", encoding="utf-8") or os.chmod(self.root / "bin" / "node", 0o755),
            "github_auth": lambda: (self.root / "bin" / "gh").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8") or os.chmod(self.root / "bin" / "gh", 0o755),
            "gmail_auth": lambda: (self.root / "bin" / "gog").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8") or os.chmod(self.root / "bin" / "gog", 0o755),
            "vercel_auth": lambda: (self.root / "bin" / "vercel").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8") or os.chmod(self.root / "bin" / "vercel", 0o755),
            "qmd_binary_exists": lambda: Path(self.env["QMD_BINARY"]).unlink(missing_ok=True),
            "qmd_index_fresh": lambda: Path(self.env["QMD_BINARY"]).write_text("#!/bin/sh\nif [ \"$1\" = \"status\" ]; then echo \"updated_at=2000-01-01T00:00:00Z\"; fi\n", encoding="utf-8") or os.chmod(Path(self.env["QMD_BINARY"]), 0o755),
            "config_valid_json": lambda: self._break_config_json(),
            "tools_md_cron_sync": lambda: (Path(self.env["WORKSPACE"]) / "TOOLS.md").write_text("## Crons\nnone\n", encoding="utf-8"),
            "tools_md_model_sync": lambda: (Path(self.env["WORKSPACE"]) / "TOOLS.md").write_text("## Models\nmissing-model\n", encoding="utf-8"),
            "agents_md_cron_refs": lambda: (Path(self.env["WORKSPACE"]) / "AGENTS.md").write_text("Dream cycle at 10:00\n", encoding="utf-8"),
            "stale_blocker_detection": lambda: (Path(self.env["MEMORY_DIR"]) / f"{self.now.date().isoformat()}.md").write_text("blocked: Superseded item\n", encoding="utf-8"),
        }

        for check_name, mutate in bad_mutations.items():
            self._build_fixture()
            mutate()
            bad = json.loads(self._run().stdout)
            all_checks = {}
            for category in bad["categories"].values():
                for check in category["checks"]:
                    all_checks[check["name"]] = check["status"]
            self.assertIn(check_name, all_checks)
            self.assertNotEqual(all_checks[check_name], "pass", f"expected bad path for {check_name}")

    def _read_cron(self):
        return json.loads(Path(self.env["CRON_STATE_PATH"]).read_text(encoding="utf-8"))

    def _write_cron(self, data):
        self._write_json(Path(self.env["CRON_STATE_PATH"]), data)

    def _tweak_cron(self, name, updates):
        data = self._read_cron()
        for cron in data["crons"]:
            if cron["name"] == name:
                cron.update(updates)
        self._write_cron(data)

    def _append_cron(self, cron):
        data = self._read_cron()
        data["crons"].append(cron)
        self._write_cron(data)


if __name__ == "__main__":
    unittest.main()
