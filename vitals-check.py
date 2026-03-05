#!/usr/bin/env python3
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

WORKSPACE = Path(os.environ.get("WORKSPACE", str(Path.home() / ".openclaw" / "workspace")))
PRAECO_WORKSPACE = Path(os.environ.get("PRAECO_WORKSPACE", str(WORKSPACE / "praeco")))
TELATIX_WORKSPACE = Path(os.environ.get("TELATIX_WORKSPACE", str(Path.home() / ".openclaw" / "workspace-telatix")))
OPENCLAW_DIR = Path(os.environ.get("OPENCLAW_DIR", str(Path.home() / ".openclaw")))
CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", str(OPENCLAW_DIR / "openclaw.json")))
QMD_BINARY = Path(os.environ.get("QMD_BINARY", str(Path.home() / ".bun" / "bin" / "qmd")))
SKILLS_GLOBAL = Path(os.environ.get("SKILLS_GLOBAL", str(OPENCLAW_DIR / "skills")))
SKILLS_LUMEN = Path(os.environ.get("SKILLS_LUMEN", str(WORKSPACE / "skills")))
SKILLS_TELATIX = Path(os.environ.get("SKILLS_TELATIX", str(TELATIX_WORKSPACE / "skills")))
MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", str(WORKSPACE / "memory")))
AUDITS_DIR = Path(os.environ.get("AUDITS_DIR", str(MEMORY_DIR / "audits")))
TIMEZONE = "Europe/Rome"
CRON_STATE_PATH = Path(os.environ.get("CRON_STATE_PATH", "/tmp/vitals-cron-state.json"))
CHECKSUM_PATH = Path(os.environ.get("CHECKSUM_PATH", "/tmp/vitals-checksums.json"))

ALLOWED_CATEGORIES = {"status", "decision", "milestone", "preference", "relationship", "strategy", "deadline"}
# Skills that use non-standard frontmatter (internal agent skills, not packaged as OpenClaw skills)
FRONTMATTER_EXCEPTIONS = {"websmith"}
# Praeco dream cycle created 2026-03-02 — notes before this date are expected to be missing
PRAECO_CYCLE_START = "2026-03-02"
# Known skill name conflicts that are intentional (global symlinks + workspace source)
KNOWN_NAME_CONFLICTS = {"ui-ux-pro-max", "memory-optimizer", "coding-agent-loops"}
# Entities intentionally allowed to have no relatedEntities links
RELATIONSHIP_ISLAND_EXCEPTIONS = {
    "projects/adrialudi",
    "projects/kasica",
    "projects/worthclip",
    "areas/people/example-person",
}
REQUIRED_FACT_FIELDS = {"id", "fact", "category", "timestamp", "source", "status", "lastAccessed", "accessCount"}
KNOWN_MODELS: set = set()  # auto-discovered from openclaw.json at runtime


class VitalsAuditor:
    def __init__(self) -> None:
        self.tz = ZoneInfo(TIMEZONE)
        self.start = time.time()
        self.now = datetime.now(self.tz)
        self.discovery_warnings: List[str] = []
        self.agents = self._discover_agents()
        self.agents_by_id = {a["id"]: a for a in self.agents}
        self.discovery = self._build_discovery_payload()
        self.known_models = self._discover_models()
        self.manifests = self._load_manifests()
        self.categories: Dict[str, Dict[str, Any]] = {}
        self.stats: Dict[str, Any] = {
            "total_facts": 0,
            "total_entities": 0,
            "hot_facts": 0,
            "warm_facts": 0,
            "cold_facts": 0,
            "disk_workspace_mb": 0,
            "disk_sessions_mb": 0,
            "backup_commits_24h": 0,
            "cron_success_rate_7d": 0.0,
        }
        self._items_cache: Optional[List[Dict[str, Any]]] = None
        self._cron_cache: Optional[Dict[str, Any]] = None

    def run(self) -> Tuple[int, Dict[str, Any]]:
        plan = [
            ("memory_integrity", self.memory_checks),
            ("knowledge_graph_quality", self.knowledge_graph_checks),
            ("daily_notes_memory", self.daily_notes_checks),
            ("tasks_audit", self.tasks_checks),
            ("cron_health", self.cron_checks),
            ("skills_validation", self.skills_checks),
            ("cross_agent_consistency", self.cross_agent_checks),
            ("filesystem_git", self.filesystem_git_checks),
            ("auth_external_services", self.auth_checks),
            ("config_documentation_sync", self.config_sync_checks),
            ("morning_brief_precheck", self.morning_brief_checks),
        ]
        for category, fn in plan:
            self.categories[category] = {"status": "pass", "checks": []}
            fn(category)
            self.categories[category]["status"] = self._aggregate_category_status(self.categories[category]["checks"])

        summary = {"total_checks": 0, "pass": 0, "warn": 0, "fail": 0}
        for category in self.categories.values():
            for check in category["checks"]:
                summary["total_checks"] += 1
                st = check.get("status", "warn")
                if st == "pass":
                    summary["pass"] += 1
                elif st == "fail":
                    summary["fail"] += 1
                else:
                    summary["warn"] += 1

        payload = {
            "timestamp": self.now.isoformat(),
            "duration_seconds": round(time.time() - self.start, 3),
            "summary": summary,
            "categories": self.categories,
            "stats": self.stats,
            "discovery": self.discovery,
        }
        if summary["fail"] > 0:
            code = 2
        elif summary["warn"] > 0:
            code = 1
        else:
            code = 0
        return code, payload

    def add_check(self, category: str, name: str, runner) -> None:
        try:
            status, details, entity = runner()
            check = {"name": name, "status": status, "details": details}
            if entity:
                check["entity"] = entity
        except Exception as exc:  # nosec - explicit graceful handling
            check = {"name": name, "status": "error", "details": f"exception: {exc}"}
        self.categories[category]["checks"].append(check)

    def _aggregate_category_status(self, checks: List[Dict[str, Any]]) -> str:
        statuses = {c.get("status") for c in checks}
        if "fail" in statuses:
            return "fail"
        if "warn" in statuses or "error" in statuses:
            return "warn"
        return "pass"

    def _run_cmd(self, cmd: List[str], timeout: int = 15, env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None) -> Dict[str, Any]:
        try:
            cp = subprocess.run(
                cmd,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
                cwd=str(cwd) if cwd else None,
            )
            return {"ok": cp.returncode == 0, "code": cp.returncode, "stdout": cp.stdout.strip(), "stderr": cp.stderr.strip(), "timeout": False}
        except subprocess.TimeoutExpired:
            return {"ok": False, "code": None, "stdout": "", "stderr": "timeout", "timeout": True}
        except Exception as exc:  # nosec
            return {"ok": False, "code": None, "stdout": "", "stderr": str(exc), "timeout": False}

    def _parse_dt(self, value: Any) -> Optional[datetime]:
        if not value or not isinstance(value, str):
            return None
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.tz)
            return dt.astimezone(self.tz)
        except Exception:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", value)
            if m:
                try:
                    return datetime.fromisoformat(m.group(1)).replace(tzinfo=self.tz)
                except Exception:
                    return None
            return None

    def _safe_read_text(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _expand_path(self, value: Any) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        return Path(str(value)).expanduser()

    def _discover_agents(self) -> List[Dict[str, Any]]:
        fallback = [
            {"id": "main", "workspace": WORKSPACE},
            {"id": "praeco", "workspace": PRAECO_WORKSPACE},
            {"id": "telatix", "workspace": TELATIX_WORKSPACE},
        ]
        data, err = self._load_config()
        if err or not isinstance(data, dict):
            self.discovery_warnings.append(f"config discovery fallback: {err or 'unknown'}")
            return fallback
        agents_cfg = data.get("agents")
        if not isinstance(agents_cfg, dict):
            self.discovery_warnings.append("config missing agents section; using fallback")
            return fallback

        defaults = agents_cfg.get("defaults") if isinstance(agents_cfg.get("defaults"), dict) else {}
        default_workspace = self._expand_path(defaults.get("workspace", WORKSPACE))
        entries = agents_cfg.get("list")
        if not isinstance(entries, list):
            self.discovery_warnings.append("config agents.list invalid; using fallback")
            return fallback

        out: List[Dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            aid = str(entry.get("id", "")).strip()
            if not aid:
                continue
            if entry.get("workspace"):
                ws = self._expand_path(entry["workspace"])
            elif aid == "main":
                ws = default_workspace
            else:
                ws = self._expand_path(Path.home() / ".openclaw" / f"workspace-{aid}")
            out.append({"id": aid, "workspace": ws})

        if not out:
            self.discovery_warnings.append("no agents discovered in config; using fallback")
            return fallback
        return out

    def _build_discovery_payload(self) -> Dict[str, Any]:
        entities_found = 0
        for agent in self.agents:
            mem = agent["workspace"] / "memory"
            if mem.exists():
                entities_found += len([p for p in mem.glob("**/items.json") if p.is_file()])
        return {
            "agents_found": [a["id"] for a in self.agents],
            "agents_with_manifest": [],
            "crons_found": 0,
            "entities_found": entities_found,
            "config_source": str(CONFIG_PATH),
            "warnings": self.discovery_warnings,
        }

    def _discover_models(self) -> set:
        """Auto-discover configured models from openclaw.json."""
        models: set = set()
        data, err = self._load_config()
        if err or not isinstance(data, dict):
            return models
        def _extract(obj: Any) -> None:
            if isinstance(obj, str) and "/" in obj:
                models.add(obj)
            elif isinstance(obj, dict):
                for key in ("default", "primary", "model"):
                    if isinstance(obj.get(key), str):
                        _extract(obj[key])
                for key in ("fallbacks",):
                    if isinstance(obj.get(key), list):
                        for item in obj[key]:
                            _extract(item)
            elif isinstance(obj, list):
                for item in obj:
                    _extract(item)
        agents_cfg = data.get("agents", {})
        defaults = agents_cfg.get("defaults", {})
        _extract(defaults.get("model"))
        for agent in agents_cfg.get("list", []):
            if isinstance(agent, dict):
                _extract(agent.get("model"))
        return models

    def _load_manifest(self, agent: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        path = agent["workspace"] / "vitals.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_path"] = path
                return data
        except Exception:
            self.discovery_warnings.append(f"invalid manifest: {path}")
        return None

    def _load_manifests(self) -> Dict[str, Dict[str, Any]]:
        manifests: Dict[str, Dict[str, Any]] = {}
        for agent in self.agents:
            manifest = self._load_manifest(agent)
            if manifest:
                manifests[agent["id"]] = manifest
        self.discovery["agents_with_manifest"] = sorted(list(manifests.keys()))
        return manifests

    def _agent_workspace(self, agent_id: str, fallback: Path) -> Path:
        rec = self.agents_by_id.get(agent_id)
        if not rec:
            return fallback
        return rec["workspace"]

    def _agent_workspaces(self) -> List[Path]:
        out: List[Path] = []
        seen = set()
        for rec in self.agents:
            ws = rec["workspace"]
            key = str(ws.resolve()) if ws.exists() else str(ws)
            if key in seen:
                continue
            seen.add(key)
            out.append(ws)
        return out

    def _load_items(self) -> List[Dict[str, Any]]:
        if self._items_cache is not None:
            return self._items_cache

        out: List[Dict[str, Any]] = []
        if not MEMORY_DIR.exists():
            self._items_cache = out
            return out

        for path in sorted(MEMORY_DIR.glob("**/items.json")):
            rel_parent = path.parent.relative_to(MEMORY_DIR).as_posix()
            rec = {"path": path, "entity": rel_parent, "data": [], "error": None}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rec["data"] = data
                else:
                    rec["error"] = "not a list"
            except Exception as exc:
                rec["error"] = str(exc)
            out.append(rec)

        hot = warm = cold = total = 0
        for rec in out:
            for fact in rec["data"]:
                if not isinstance(fact, dict):
                    continue
                total += 1
                last = self._parse_dt(fact.get("lastAccessed"))
                if not last:
                    cold += 1
                    continue
                age = (self.now - last).days
                if age <= 7:
                    hot += 1
                elif age <= 30:
                    warm += 1
                else:
                    cold += 1
        self.stats["total_facts"] = total
        self.stats["total_entities"] = len(out)
        self.stats["hot_facts"] = hot
        self.stats["warm_facts"] = warm
        self.stats["cold_facts"] = cold

        self._items_cache = out
        return out

    def _load_cron(self) -> Tuple[Optional[List[Dict[str, Any]]], str]:
        if self._cron_cache is not None:
            return self._cron_cache.get("crons"), self._cron_cache.get("status", "ok")

        if not CRON_STATE_PATH.exists():
            self._cron_cache = {"crons": None, "status": "missing"}
            return None, "missing"
        try:
            data = json.loads(CRON_STATE_PATH.read_text(encoding="utf-8"))
            crons = (data.get("jobs") or data.get("crons")) if isinstance(data, dict) else data
            if not isinstance(crons, list):
                self._cron_cache = {"crons": None, "status": "invalid"}
                return None, "invalid"
            generated = None
            if isinstance(data, dict):
                generated = data.get("generatedAtMs")
            stale = False
            if isinstance(generated, (int, float)):
                generated_dt = datetime.fromtimestamp(generated / 1000, tz=self.tz)
                stale = self.now - generated_dt > timedelta(hours=1)
            status = "stale" if stale else "ok"
            self.discovery["crons_found"] = len([c for c in crons if isinstance(c, dict)])
            self._cron_cache = {"crons": crons, "status": status}
            return crons, status
        except Exception:
            self._cron_cache = {"crons": None, "status": "invalid"}
            return None, "invalid"

    def _dir_size_mb(self, path: Path) -> float:
        total = 0
        try:
            for p in path.glob("**/*"):
                if p.is_file():
                    total += p.stat().st_size
        except Exception:
            return 0.0
        return total / (1024 * 1024)

    def _latest_daily(self, base: Path) -> Optional[Path]:
        files = [p for p in base.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", p.name)] if base.exists() else []
        return sorted(files)[-1] if files else None

    def _list_entity_dirs(self) -> List[Path]:
        dirs: List[Path] = []
        for root in [MEMORY_DIR / "projects", MEMORY_DIR / "areas"]:
            if not root.exists():
                continue
            for p in root.glob("**"):
                if p.is_dir() and (p / "items.json").exists() or (p / "summary.md").exists():
                    dirs.append(p)
        unique = []
        seen = set()
        for d in dirs:
            r = d.resolve()
            if r not in seen:
                seen.add(r)
                unique.append(d)
        return unique

    # 1) MEMORY INTEGRITY
    def memory_checks(self, category: str) -> None:
        self.add_check(category, "json_valid", self._c_json_valid)
        self.add_check(category, "duplicate_ids", self._c_duplicate_ids)
        self.add_check(category, "required_fields", self._c_required_fields)
        self.add_check(category, "empty_facts", self._c_empty_facts)
        self.add_check(category, "category_valid", self._c_category_valid)
        self.add_check(category, "timestamp_valid", self._c_timestamp_valid)
        self.add_check(category, "supersede_chain_valid", self._c_supersede_chain_valid)
        self.add_check(category, "orphaned_supersede", self._c_orphaned_supersede)
        self.add_check(category, "access_count_sane", self._c_access_count_sane)
        self.add_check(category, "duplicate_fact_text", self._c_duplicate_fact_text)
        self.add_check(category, "id_sequence", self._c_id_sequence)
        self.add_check(category, "cold_fact_ratio", self._c_cold_fact_ratio)
        self.add_check(category, "stale_active_blockers", self._c_stale_active_blockers)

    def _c_json_valid(self):
        bad = [r["entity"] for r in self._load_items() if r["error"]]
        if bad:
            return "fail", f"invalid json: {', '.join(bad[:5])}", None
        return "pass", "all items.json files parse", None

    def _iter_facts(self):
        for rec in self._load_items():
            if rec["error"]:
                continue
            for fact in rec["data"]:
                if isinstance(fact, dict):
                    yield rec, fact

    def _c_duplicate_ids(self):
        bad = []
        for rec in self._load_items():
            ids = [f.get("id") for f in rec["data"] if isinstance(f, dict)]
            if len(ids) != len(set(ids)):
                bad.append(rec["entity"])
        if bad:
            return "fail", f"duplicate ids in {', '.join(bad[:5])}", None
        return "pass", "ids are unique", None

    def _c_required_fields(self):
        misses = []
        for rec, fact in self._iter_facts():
            missing = REQUIRED_FACT_FIELDS - set(fact.keys())
            if missing:
                misses.append(f"{rec['entity']}:{fact.get('id')} missing {sorted(missing)}")
        if misses:
            return "fail", "; ".join(misses[:5]), None
        return "pass", "all required fields present", None

    def _c_empty_facts(self):
        bad = [f"{r['entity']}:{f.get('id')}" for r, f in self._iter_facts() if str(f.get("fact", "")).strip() == ""]
        if bad:
            return "fail", f"empty fact text: {', '.join(bad[:5])}", None
        return "pass", "no empty fact texts", None

    def _c_category_valid(self):
        bad = [f"{r['entity']}:{f.get('id')}" for r, f in self._iter_facts() if f.get("category") not in ALLOWED_CATEGORIES]
        if bad:
            return "fail", f"invalid categories: {', '.join(bad[:5])}", None
        return "pass", "categories valid", None

    def _c_timestamp_valid(self):
        bad = []
        cutoff = self.now + timedelta(days=1)
        for rec, fact in self._iter_facts():
            ts = self._parse_dt(fact.get("timestamp"))
            if ts is None or ts > cutoff:
                bad.append(f"{rec['entity']}:{fact.get('id')}")
        if bad:
            return "fail", f"bad timestamps: {', '.join(bad[:5])}", None
        return "pass", "timestamps valid", None

    def _c_supersede_chain_valid(self):
        bad = []
        for rec in self._load_items():
            ids = {f.get("id") for f in rec["data"] if isinstance(f, dict)}
            for fact in rec["data"]:
                if not isinstance(fact, dict):
                    continue
                sup = fact.get("supersededBy")
                if sup is not None and (sup not in ids or fact.get("status") != "superseded"):
                    bad.append(f"{rec['entity']}:{fact.get('id')}")
        if bad:
            return "fail", f"invalid supersede chains: {', '.join(bad[:5])}", None
        return "pass", "supersede chains valid", None

    def _c_orphaned_supersede(self):
        bad = []
        for rec in self._load_items():
            ids = {f.get("id") for f in rec["data"] if isinstance(f, dict)}
            for fact in rec["data"]:
                if isinstance(fact, dict) and fact.get("supersededBy") is not None and fact.get("supersededBy") not in ids:
                    bad.append(f"{rec['entity']}:{fact.get('id')}")
        if bad:
            return "fail", f"orphaned supersededBy: {', '.join(bad[:5])}", None
        return "pass", "no orphaned supersedes", None

    def _c_access_count_sane(self):
        bad = [f"{r['entity']}:{f.get('id')}" for r, f in self._iter_facts() if not isinstance(f.get("accessCount"), int) or f.get("accessCount") < 0 or f.get("accessCount") > 200]
        if bad:
            return "fail", f"accessCount out of range: {', '.join(bad[:5])}", None
        return "pass", "accessCount sane", None

    def _c_duplicate_fact_text(self):
        dupes = []
        for rec in self._load_items():
            seen: Dict[str, Any] = {}
            for fact in rec["data"]:
                if not isinstance(fact, dict):
                    continue
                txt = str(fact.get("fact", "")).strip().lower()
                if not txt:
                    continue
                if txt in seen:
                    dupes.append(rec["entity"])
                    break
                seen[txt] = fact.get("id")
        if dupes:
            return "warn", f"duplicate fact text in {', '.join(dupes[:5])}", None
        return "pass", "no duplicate fact text", None

    def _c_id_sequence(self):
        bad = []
        for rec in self._load_items():
            ids = sorted([f.get("id") for f in rec["data"] if isinstance(f, dict) and isinstance(f.get("id"), int)])
            for a, b in zip(ids, ids[1:]):
                if b - a > 5:
                    bad.append(rec["entity"])
                    break
        if bad:
            return "warn", f"large id gaps in {', '.join(bad[:5])}", None
        return "pass", "id sequence gaps acceptable", None

    def _c_cold_fact_ratio(self):
        bad = []
        for rec in self._load_items():
            active = [f for f in rec["data"] if isinstance(f, dict) and f.get("status") != "superseded"]
            if not active:
                continue
            cold = 0
            for fact in active:
                last = self._parse_dt(fact.get("lastAccessed"))
                if not last or (self.now - last).days > 30:
                    cold += 1
            if cold / len(active) > 0.6:
                bad.append(rec["entity"])
        if bad:
            return "warn", f"cold fact ratio high: {', '.join(bad[:5])}", None
        return "pass", "cold fact ratio healthy", None

    def _c_stale_active_blockers(self):
        words = ("blocked", "rejected", "failed", "broken", "issue", "unresolved")
        bad = []
        for rec, fact in self._iter_facts():
            if fact.get("status") == "superseded":
                continue
            txt = str(fact.get("fact", "")).lower()
            if not any(w in txt for w in words):
                continue
            last = self._parse_dt(fact.get("lastAccessed"))
            if not last or (self.now - last).days > 14:
                bad.append(f"{rec['entity']}:{fact.get('id')}")
        if bad:
            return "warn", f"stale blockers: {', '.join(bad[:5])}", None
        return "pass", "no stale active blockers", None

    # 2) KNOWLEDGE GRAPH QUALITY
    def knowledge_graph_checks(self, category: str) -> None:
        self.add_check(category, "entity_completeness", self._c_entity_completeness)
        self.add_check(category, "summary_freshness", self._c_summary_freshness)
        self.add_check(category, "summary_items_drift", self._c_summary_items_drift)
        self.add_check(category, "relationship_islands", self._c_relationship_islands)
        self.add_check(category, "fact_velocity", self._c_fact_velocity)
        self.add_check(category, "archive_candidates", self._c_archive_candidates)
        self.add_check(category, "cross_references_valid", self._c_cross_references_valid)

    def _c_entity_completeness(self):
        missing = []
        for d in self._list_entity_dirs():
            if not (d / "items.json").exists() or not (d / "summary.md").exists():
                missing.append(d.relative_to(MEMORY_DIR).as_posix())
        if missing:
            return "fail", f"incomplete entities: {', '.join(missing[:5])}", None
        return "pass", "all entities complete", None

    def _c_summary_freshness(self):
        stale = []
        for d in self._list_entity_dirs():
            s = d / "summary.md"
            if not s.exists():
                continue
            text = self._safe_read_text(s)
            m = re.search(r"Last regenerated:\s*(\d{4}-\d{2}-\d{2})", text)
            if not m:
                stale.append(d.relative_to(MEMORY_DIR).as_posix())
                continue
            regen = self._parse_dt(m.group(1))
            if not regen or (self.now - regen).days > 14:
                stale.append(d.relative_to(MEMORY_DIR).as_posix())
        if stale:
            return "warn", f"stale summaries: {', '.join(stale[:5])}", None
        return "pass", "summaries fresh", None

    def _c_summary_items_drift(self):
        drifted = []
        by_entity = {r["entity"]: r for r in self._load_items()}
        for d in self._list_entity_dirs():
            entity = d.relative_to(MEMORY_DIR).as_posix()
            s = d / "summary.md"
            if entity not in by_entity or not s.exists():
                continue
            text = self._safe_read_text(s).lower()
            active = [f for f in by_entity[entity]["data"] if isinstance(f, dict) and f.get("status") != "superseded"]
            if not active:
                continue
            missing = 0
            sampled = 0
            for fact in active[:10]:
                last = self._parse_dt(fact.get("lastAccessed"))
                age = (self.now - last).days if last else 999
                if age > 30:
                    continue
                sampled += 1
                needle = str(fact.get("fact", "")).strip().lower()
                if needle and needle not in text:
                    missing += 1
            if sampled > 0 and (missing / sampled) >= 0.5:
                drifted.append(entity)
        if drifted:
            return "warn", f"summary drift: {', '.join(drifted[:5])}", None
        return "pass", "summary/items alignment ok", None

    def _c_relationship_islands(self):
        islands = []
        for rec in self._load_items():
            facts = [f for f in rec["data"] if isinstance(f, dict)]
            if not facts:
                continue
            all_empty = True
            for fact in facts:
                rel = fact.get("relatedEntities")
                if isinstance(rel, list) and rel:
                    all_empty = False
                    break
            if all_empty and rec["entity"] not in RELATIONSHIP_ISLAND_EXCEPTIONS:
                islands.append(rec["entity"])
        if islands:
            return "warn", f"relationship islands: {', '.join(islands[:5])}", None
        return "pass", "relationships present", None

    def _c_fact_velocity(self):
        stale = []
        for rec in self._load_items():
            latest = None
            for fact in rec["data"]:
                if not isinstance(fact, dict):
                    continue
                ts = self._parse_dt(fact.get("timestamp"))
                if ts and (latest is None or ts > latest):
                    latest = ts
            if latest is None or (self.now - latest).days >= 14:
                stale.append(rec["entity"])
        if stale:
            return "warn", f"low fact velocity: {', '.join(stale[:5])}", None
        return "pass", "fact velocity healthy", None

    def _c_archive_candidates(self):
        candidates = []
        for rec in self._load_items():
            active = [f for f in rec["data"] if isinstance(f, dict) and f.get("status") != "superseded"]
            if not active:
                continue
            if all((self.now - (self._parse_dt(f.get("lastAccessed")) or datetime(1970, 1, 1, tzinfo=self.tz))).days > 30 for f in active):
                candidates.append(rec["entity"])
        if candidates:
            return "warn", f"archive candidates: {', '.join(candidates[:5])}", None
        return "pass", "no archive candidates", None

    def _c_cross_references_valid(self):
        entities = {r["entity"] for r in self._load_items()}
        bad = []
        for rec, fact in self._iter_facts():
            rel = fact.get("relatedEntities")
            if not isinstance(rel, list):
                continue
            for target in rel:
                if target and target not in entities:
                    bad.append(f"{rec['entity']}->{target}")
        if bad:
            return "fail", f"invalid cross refs: {', '.join(bad[:5])}", None
        return "pass", "cross references valid", None

    # 3) DAILY NOTES & MEMORY.MD
    def daily_notes_checks(self, category: str) -> None:
        self.add_check(category, "daily_note_gaps", self._c_daily_note_gaps)
        self.add_check(category, "praeco_daily_note_gaps", self._c_praeco_daily_note_gaps)
        self.add_check(category, "cross_pollination_present", self._c_cross_pollination_present)
        self.add_check(category, "memory_md_guard", self._c_memory_md_guard)
        self.add_check(category, "daily_note_size", self._c_daily_note_size)

    def _check_daily_gaps(self, base: Path) -> List[str]:
        present = {p.stem for p in base.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)} if base.exists() else set()
        missing = []
        for i in range(7):
            day = (self.now.date() - timedelta(days=i)).isoformat()
            if day not in present:
                missing.append(day)
        return missing

    def _c_daily_note_gaps(self):
        missing = self._check_daily_gaps(MEMORY_DIR)
        if missing:
            return "warn", f"missing daily notes: {', '.join(missing[:3])}", None
        return "pass", "daily notes complete", None

    def _c_praeco_daily_note_gaps(self):
        praeco_workspace = self._agent_workspace("praeco", PRAECO_WORKSPACE)
        missing = [d for d in self._check_daily_gaps(praeco_workspace / "memory") if d >= PRAECO_CYCLE_START]
        if missing:
            return "warn", f"missing praeco notes: {', '.join(missing[:3])}", None
        return "pass", "praeco daily notes complete", None

    def _c_cross_pollination_present(self):
        latest = self._latest_daily(MEMORY_DIR)
        if not latest:
            return "warn", "no daily notes", None
        text = self._safe_read_text(latest).lower()
        if "cross-pollination" in text or "cross pollination" in text or "praeco" in text:
            return "pass", "cross-pollination section found", None
        return "warn", "cross-pollination section missing", None

    def _c_memory_md_guard(self):
        path = WORKSPACE / "MEMORY.md"
        text = self._safe_read_text(path).lower()
        if not text:
            return "warn", "MEMORY.md missing/unreadable", None
        names = [p["entity"].split("/")[-1].lower() for p in self._load_items() if p.get("entity")]
        suspicious = [n for n in names if n and n in text]
        if suspicious or any(k in text for k in ("status", "deadline", "milestone", "blocked")):
            return "warn", "MEMORY.md may include project/person/company specifics", None
        return "pass", "MEMORY.md guard looks clean", None

    def _c_daily_note_size(self):
        overs = []
        for p in MEMORY_DIR.glob("*.md") if MEMORY_DIR.exists() else []:
            if p.stat().st_size > 20 * 1024:
                overs.append(p.name)
        if overs:
            return "warn", f"oversized daily notes: {', '.join(overs[:5])}", None
        return "pass", "daily note sizes healthy", None

    # 4) TASKS AUDIT
    def tasks_checks(self, category: str) -> None:
        self.add_check(category, "tasks_json_valid", self._c_tasks_json_valid)
        self.add_check(category, "stale_deadlines", self._c_stale_deadlines)
        self.add_check(category, "missing_descriptions", self._c_missing_descriptions)
        self.add_check(category, "zombie_tasks", self._c_zombie_tasks)

    def _load_tasks(self) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        path = WORKSPACE / "tasks.json"
        if not path.exists():
            return None, "missing tasks.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "tasks" in data:
                data = data["tasks"]
            if not isinstance(data, list):
                return None, "tasks.json not list"
            return data, None
        except Exception as exc:
            return None, str(exc)

    def _c_tasks_json_valid(self):
        _, err = self._load_tasks()
        if err:
            return "fail", err, None
        return "pass", "tasks.json valid", None

    def _c_stale_deadlines(self):
        tasks, err = self._load_tasks()
        if err:
            return "warn", "skipped: tasks unavailable", None
        bad = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            if task.get("status") not in {"active", "pending"}:
                continue
            dd = self._parse_dt(task.get("deadline"))
            if dd and dd < self.now:
                bad.append(task.get("name", "unnamed"))
        if bad:
            return "warn", f"stale deadlines: {', '.join(bad[:5])}", None
        return "pass", "deadlines fresh", None

    def _c_missing_descriptions(self):
        tasks, err = self._load_tasks()
        if err:
            return "warn", "skipped: tasks unavailable", None
        bad = [t.get("name", "unnamed") for t in tasks if isinstance(t, dict) and str(t.get("description", "")).strip() == ""]
        if bad:
            return "warn", f"missing descriptions: {', '.join(bad[:5])}", None
        return "pass", "task descriptions present", None

    def _c_zombie_tasks(self):
        tasks, err = self._load_tasks()
        if err:
            return "warn", "skipped: tasks unavailable", None
        corpus = []
        for p in MEMORY_DIR.glob("*.md") if MEMORY_DIR.exists() else []:
            corpus.append(self._safe_read_text(p).lower())
        joined = "\n".join(corpus)
        bad = []
        for t in tasks:
            if not isinstance(t, dict) or t.get("status") != "active":
                continue
            keys = [str(t.get("name", "")).strip().lower(), str(t.get("description", "")).strip().lower()]
            keys = [k for k in keys if k]
            if keys and not any(k in joined for k in keys):
                bad.append(t.get("name", "unnamed"))
        if bad:
            return "warn", f"zombie tasks: {', '.join(bad[:5])}", None
        return "pass", "no zombie tasks", None

    # 5) CRON HEALTH
    def cron_checks(self, category: str) -> None:
        self.add_check(category, "expected_crons_exist", self._c_expected_crons_exist)
        self.add_check(category, "all_enabled", self._c_all_enabled)
        self.add_check(category, "consecutive_errors", self._c_consecutive_errors)
        self.add_check(category, "timeout_errors", self._c_timeout_errors)
        self.add_check(category, "timezone_consistency", self._c_timezone_consistency)
        self.add_check(category, "schedule_collision", self._c_schedule_collision)
        self.add_check(category, "dream_cycle_ordering", self._c_dream_cycle_ordering)
        self.add_check(category, "stale_runs", self._c_stale_runs)
        self.add_check(category, "delivery_sanity", self._c_delivery_sanity)
        self.add_check(category, "orphan_one_shots", self._c_orphan_one_shots)
        self.add_check(category, "model_valid", self._c_model_valid)

    def _with_crons(self) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        crons, status = self._load_cron()
        if crons is None:
            return None, f"cron state {status}"
        if status != "ok":
            return crons, f"cron state {status}"
        return crons, None

    def _cron_by_name(self) -> Dict[str, Dict[str, Any]]:
        crons, _ = self._with_crons()
        if not crons:
            return {}
        return {c.get("name"): c for c in crons if isinstance(c, dict) and c.get("name")}

    def _cron_schedule(self, cron: Dict[str, Any]) -> Tuple[str, str, str]:
        raw = cron.get("schedule")
        if isinstance(raw, dict):
            expr = str(raw.get("expr", "")).strip()
            kind = str(raw.get("kind", cron.get("kind", "cron"))).strip() or "cron"
            tz = str(raw.get("tz", cron.get("tz", ""))).strip()
            return expr, kind, tz
        return str(raw or "").strip(), str(cron.get("kind", "cron")).strip() or "cron", str(cron.get("tz", "")).strip()

    def _c_expected_crons_exist(self):
        crons, err = self._with_crons()
        if crons is None:
            return "warn", err or "cron unavailable", None
        enabled = [c for c in crons if isinstance(c, dict) and c.get("enabled", False)]
        if not enabled:
            return "fail", "no enabled crons found", None
        if err:
            return "warn", err, None
        return "pass", f"enabled crons discovered: {len(enabled)}", None

    def _c_all_enabled(self):
        crons, _ = self._with_crons()
        if not crons:
            return "warn", "cron unavailable", None
        bad = [str(c.get("name")) for c in crons if isinstance(c, dict) and c.get("enabled") is False]
        if bad:
            return "fail", f"disabled crons: {', '.join(bad[:5])}", None
        return "pass", "all discovered crons enabled", None

    def _c_consecutive_errors(self):
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        fail = [n for n, c in by.items() if c.get("consecutiveErrors", 0) >= 2]
        warn = [n for n, c in by.items() if c.get("consecutiveErrors", 0) == 1]
        if fail:
            return "fail", f"consecutive errors >=2: {', '.join(fail[:5])}", None
        if warn:
            return "warn", f"consecutive errors =1: {', '.join(warn[:5])}", None
        return "pass", "no consecutive errors", None

    def _c_timeout_errors(self):
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        bad = [n for n, c in by.items() if "timed out" in str(c.get("lastError", "")).lower()]
        if bad:
            return "warn", f"timeout errors in: {', '.join(bad[:5])}", None
        return "pass", "no timeout errors", None

    def _c_timezone_consistency(self):
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        bad = []
        for n, c in by.items():
            _, kind, tz = self._cron_schedule(c)
            if kind != "cron":
                continue
            if not tz:
                bad.append(n)
        if bad:
            return "warn", f"missing tz on: {', '.join(bad[:5])}", None
        return "pass", "cron timezones set", None

    def _c_schedule_collision(self):
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        seen: Dict[str, str] = {}
        dup = []
        for n, c in by.items():
            sched = str(c.get("schedule", "")).strip()
            if not sched:
                continue
            if sched in seen:
                dup.append(f"{seen[sched]} & {n}")
            else:
                seen[sched] = n
        if dup:
            return "warn", f"schedule collisions: {', '.join(dup[:5])}", None
        return "pass", "no schedule collisions", None

    def _parse_hm(self, expr: str) -> Optional[Tuple[int, int]]:
        parts = expr.split()
        if len(parts) < 2:
            return None
        m, h = parts[0], parts[1]
        if m.isdigit() and h.isdigit():
            return int(h), int(m)
        return None

    def _c_dream_cycle_ordering(self):
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        a = by.get("praeco-dream-cycle")
        b = by.get("dream-cycle")
        if not a or not b:
            return "warn", "missing dream cycle cron(s)", None
        ha = self._parse_hm(self._cron_schedule(a)[0])
        hb = self._parse_hm(self._cron_schedule(b)[0])
        if not ha or not hb:
            return "warn", "could not parse dream-cycle schedule", None
        if ha < hb:
            return "pass", "praeco-dream-cycle runs before dream-cycle", None
        return "fail", "praeco-dream-cycle does not run before dream-cycle", None

    def _expected_interval_sec(self, expr: str) -> int:
        parts = expr.split()
        if len(parts) < 5:
            return 86400
        m, h, dom, _mo, dow = parts[:5]
        if m.startswith("*/"):
            try:
                return int(m[2:]) * 60
            except Exception:
                return 3600
        if h == "*":
            return 3600
        if dow != "*":
            return 7 * 86400
        if dom != "*":
            return 30 * 86400
        return 86400

    def _c_stale_runs(self):
        crons, _ = self._with_crons()
        if not crons:
            return "warn", "cron unavailable", None
        stale = []
        for c in crons:
            if not isinstance(c, dict) or not c.get("enabled", False):
                continue
            n = str(c.get("name", "unnamed"))
            last_ms = c.get("lastRunAtMs")
            if not isinstance(last_ms, (int, float)):
                continue
            last = datetime.fromtimestamp(last_ms / 1000, tz=self.tz)
            interval = self._expected_interval_sec(self._cron_schedule(c)[0])
            if (self.now - last).total_seconds() > (2 * interval) + 300:
                stale.append(n)
        if stale:
            return "warn", f"stale runs: {', '.join(stale[:5])}", None
        return "pass", "runs are fresh", None

    def _c_delivery_sanity(self):
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        bad = []
        for n, c in by.items():
            delivery = c.get("delivery")
            if isinstance(delivery, dict) and delivery.get("channelEnabled") is False:
                bad.append(n)
        if bad:
            return "warn", f"delivery points to disabled channels: {', '.join(bad[:5])}", None
        return "pass", "delivery sanity ok", None

    def _c_orphan_one_shots(self):
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        bad = []
        for n, c in by.items():
            if c.get("kind") == "at" and c.get("lastRunAtMs") and not c.get("deleteAfterRun", False):
                bad.append(n)
        if bad:
            return "warn", f"orphan one-shots: {', '.join(bad[:5])}", None
        return "pass", "one-shot cleanup ok", None

    def _c_model_valid(self):
        if not self.known_models:
            return "warn", "no models discovered from config", None
        by = self._cron_by_name()
        if not by:
            return "warn", "cron unavailable", None
        bad = []
        for n, c in by.items():
            model = str(c.get("model", "")).strip()
            if model and model not in self.known_models:
                bad.append(f"{n}:{model}")
        if bad:
            return "warn", f"unknown models: {', '.join(bad[:5])}", None
        return "pass", f"cron models valid ({len(self.known_models)} configured)", None

    # 6) SKILLS VALIDATION
    def skills_checks(self, category: str) -> None:
        self.add_check(category, "skill_md_present", self._c_skill_md_present)
        self.add_check(category, "frontmatter_valid", self._c_frontmatter_valid)
        self.add_check(category, "description_length", self._c_description_length)
        self.add_check(category, "symlink_integrity", self._c_symlink_integrity)
        self.add_check(category, "script_exists", self._c_script_exists)
        self.add_check(category, "name_conflicts", self._c_name_conflicts)
        self.add_check(category, "data_files_exist", self._c_data_files_exist)

    def _skill_dirs(self) -> List[Tuple[str, Path]]:
        roots = [("global", SKILLS_GLOBAL), ("lumen", SKILLS_LUMEN), ("telatix", SKILLS_TELATIX)]
        out = []
        for source, root in roots:
            if not root.exists():
                continue
            for child in root.iterdir():
                if child.is_dir():
                    out.append((source, child))
        return out

    def _parse_frontmatter(self, text: str) -> Optional[Dict[str, str]]:
        m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
        if not m:
            return None
        out: Dict[str, str] = {}
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
        return out

    def _c_skill_md_present(self):
        missing = []
        for _, d in self._skill_dirs():
            if not (d / "SKILL.md").exists():
                missing.append(d.name)
        if missing:
            return "fail", f"missing SKILL.md: {', '.join(missing[:5])}", None
        return "pass", "all skills have SKILL.md", None

    def _c_frontmatter_valid(self):
        bad = []
        for _, d in self._skill_dirs():
            p = d / "SKILL.md"
            if not p.exists():
                continue
            if d.name in FRONTMATTER_EXCEPTIONS:
                continue
            fm = self._parse_frontmatter(self._safe_read_text(p))
            if not fm or not fm.get("name") or not fm.get("description"):
                bad.append(d.name)
        if bad:
            return "fail", f"invalid frontmatter: {', '.join(bad[:5])}", None
        return "pass", "frontmatter valid", None

    def _c_description_length(self):
        bad = []
        for _, d in self._skill_dirs():
            p = d / "SKILL.md"
            if not p.exists():
                continue
            fm = self._parse_frontmatter(self._safe_read_text(p)) or {}
            desc = fm.get("description", "")
            if len(desc) < 50 or len(desc) > 500:
                bad.append(d.name)
        if bad:
            return "warn", f"description length out of bounds: {', '.join(bad[:5])}", None
        return "pass", "description lengths valid", None

    def _c_symlink_integrity(self):
        bad = []
        for _, d in self._skill_dirs():
            for p in d.glob("**/*"):
                if p.is_symlink():
                    try:
                        target = p.resolve(strict=True)
                        if not target.exists():
                            bad.append(str(p))
                    except Exception:
                        bad.append(str(p))
        if bad:
            return "fail", f"broken symlinks: {', '.join(bad[:3])}", None
        return "pass", "symlinks valid", None

    def _c_script_exists(self):
        bad = []
        for _, d in self._skill_dirs():
            scripts = d / "scripts"
            if not scripts.exists():
                continue
            pys = list(scripts.glob("*.py"))
            shs = list(scripts.glob("*.sh"))
            if not pys and not shs:
                bad.append(d.name)
                continue
            for py in pys:
                try:
                    ast.parse(self._safe_read_text(py))
                except Exception:
                    bad.append(d.name)
                    break
        if bad:
            return "fail", f"script issues: {', '.join(bad[:5])}", None
        return "pass", "scripts present and parseable", None

    def _c_name_conflicts(self):
        names: Dict[str, List[str]] = {}
        for source, d in self._skill_dirs():
            p = d / "SKILL.md"
            if not p.exists():
                continue
            fm = self._parse_frontmatter(self._safe_read_text(p)) or {}
            name = fm.get("name")
            if not name:
                continue
            names.setdefault(name, []).append(f"{source}:{d.name}")
        bad = [f"{name} -> {', '.join(srcs)}" for name, srcs in names.items()
               if len(srcs) > 1 and name not in KNOWN_NAME_CONFLICTS]
        if bad:
            return "warn", f"name conflicts: {', '.join(bad[:3])}", None
        return "pass", "no unexpected skill name conflicts", None

    def _c_data_files_exist(self):
        p = SKILLS_GLOBAL / "ui-ux-pro-max" / "data"
        if not p.exists():
            return "warn", "ui-ux-pro-max data dir missing", None
        csvs = list(p.glob("*.csv"))
        if not csvs:
            return "warn", "ui-ux-pro-max missing csv data files", None
        empty = [c.name for c in csvs if c.stat().st_size == 0]
        if empty:
            return "warn", f"empty data files: {', '.join(empty[:5])}", None
        return "pass", "data files present", None

    # 7) CROSS-AGENT CONSISTENCY
    def cross_agent_checks(self, category: str) -> None:
        self.add_check(category, "workspace_exists", self._c_workspace_exists)
        self.add_check(category, "workspace_writable", self._c_workspace_writable)
        self.add_check(category, "workspace_size", self._c_workspace_size)
        self.add_check(category, "praeco_brand_voice_checksum", self._c_praeco_brand_voice_checksum)
        self.add_check(category, "praeco_content_pipeline_current", self._c_praeco_content_pipeline_current)
        self.add_check(category, "agents_md_session_keys", self._c_agents_md_session_keys)
        self.add_check(category, "manifest_required_files", self._c_manifest_required_files)
        self.add_check(category, "manifest_required_dirs", self._c_manifest_required_dirs)
        self.add_check(category, "manifest_health_signals", self._c_manifest_health_signals)
        self.add_check(category, "cross_pollination_graph", self._c_cross_pollination_graph)

    def _c_workspace_exists(self):
        missing = [str(p) for p in self._agent_workspaces() if not p.exists() or not p.is_dir()]
        if missing:
            return "fail", f"missing workspaces: {', '.join(missing[:3])}", None
        return "pass", "all workspaces exist", None

    def _c_workspace_writable(self):
        bad = []
        for p in self._agent_workspaces():
            if not os.access(p, os.W_OK):
                bad.append(str(p))
        if bad:
            return "fail", f"non-writable workspaces: {', '.join(bad[:3])}", None
        return "pass", "workspaces writable", None

    def _c_workspace_size(self):
        bad = []
        for p in self._agent_workspaces():
            mb = self._dir_size_mb(p)
            if mb > 200:
                bad.append(f"{p.name}:{mb:.1f}MB")
        if bad:
            return "warn", f"large workspace(s): {', '.join(bad[:3])}", None
        return "pass", "workspace sizes normal", None

    def _sha256(self, path: Path) -> Optional[str]:
        if not path.exists() or not path.is_file():
            return None
        h = hashlib.sha256()
        with path.open("rb") as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()

    def _c_praeco_brand_voice_checksum(self):
        praeco_workspace = self._agent_workspace("praeco", PRAECO_WORKSPACE)
        monitored: Dict[str, Path] = {
            "praeco_brand_voice": praeco_workspace / "BRAND_VOICE.md",
            "praeco_content_lessons": praeco_workspace / "memory" / "strategy" / "content-lessons.md",
        }
        for aid, manifest in self.manifests.items():
            ws = self._agent_workspace(aid, WORKSPACE)
            for item in manifest.get("monitored_files", []):
                if not isinstance(item, dict):
                    continue
                rel = item.get("path")
                if not rel:
                    continue
                key = f"{aid}:{rel}"
                monitored[key] = ws / str(rel)

        current = {key: self._sha256(path) for key, path in monitored.items()}
        prev = {}
        if CHECKSUM_PATH.exists():
            try:
                prev = json.loads(CHECKSUM_PATH.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        changed = []
        for k, v in current.items():
            if v is None:
                changed.append(f"{k}:missing")
            elif prev and prev.get(k) and prev.get(k) != v:
                changed.append(k)
        if changed:
            return "warn", f"monitored file changed: {', '.join(changed[:5])}", None
        return "pass", "monitored checksums stable", None

    def _c_praeco_content_pipeline_current(self):
        target = self.now.strftime("%Y-%m")
        files = []
        praeco_workspace = self._agent_workspace("praeco", PRAECO_WORKSPACE)
        if praeco_workspace.exists():
            files = [p.name for p in praeco_workspace.glob("**/*") if p.is_file() and "content-sequence" in p.name]
        if not files:
            return "warn", "content-sequence file missing", None
        if any(target in name for name in files):
            return "pass", "content pipeline current", None
        return "warn", f"content-sequence not aligned with {target}", None

    def _c_manifest_required_files(self):
        missing = []
        for aid, manifest in self.manifests.items():
            ws = self._agent_workspace(aid, WORKSPACE)
            for rel in manifest.get("required_files", []):
                p = ws / str(rel)
                if not p.exists() or not p.is_file():
                    missing.append(f"{aid}:{rel}")
        if missing:
            return "fail", f"manifest required files missing: {', '.join(missing[:5])}", None
        return "pass", "manifest required files present", None

    def _c_manifest_required_dirs(self):
        missing = []
        for aid, manifest in self.manifests.items():
            ws = self._agent_workspace(aid, WORKSPACE)
            for rel in manifest.get("required_dirs", []):
                p = ws / str(rel)
                if not p.exists() or not p.is_dir():
                    missing.append(f"{aid}:{rel}")
        if missing:
            return "fail", f"manifest required dirs missing: {', '.join(missing[:5])}", None
        return "pass", "manifest required dirs present", None

    def _signal_result(self, severity: str) -> str:
        return "fail" if str(severity).lower() == "fail" else "warn"

    def _eval_health_signal(self, ws: Path, signal: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        stype = str(signal.get("type", "")).strip()
        path = signal.get("path")
        severity = str(signal.get("severity", "warn"))
        if not stype or not path:
            return None
        if stype == "file_freshness":
            max_days = int(signal.get("max_stale_days", 30))
            matches = list(ws.glob(str(path)))
            if not matches:
                return self._signal_result(severity), f"no files matched {path}"
            newest = max([datetime.fromtimestamp(p.stat().st_mtime, tz=self.tz) for p in matches if p.exists()], default=None)
            if not newest or (self.now - newest).days > max_days:
                return self._signal_result(severity), f"stale file(s) for {path}"
            return None
        if stype == "dir_activity":
            max_days = int(signal.get("max_gap_days", 3))
            d = ws / str(path)
            if not d.exists() or not d.is_dir():
                return self._signal_result(severity), f"missing dir {path}"
            latest = None
            for p in d.glob("**/*"):
                if p.is_file():
                    m = datetime.fromtimestamp(p.stat().st_mtime, tz=self.tz)
                    if latest is None or m > latest:
                        latest = m
            if latest is None or (self.now - latest).days > max_days:
                return self._signal_result(severity), f"inactive dir {path}"
            return None
        if stype == "file_contains":
            p = ws / str(path)
            if not p.exists() or not p.is_file():
                return self._signal_result(severity), f"missing file {path}"
            text = self._safe_read_text(p)
            pattern = str(signal.get("pattern", ""))
            if not re.search(pattern, text, flags=re.MULTILINE):
                return self._signal_result(severity), f"pattern not found in {path}"
            return None
        if stype == "file_max_size_kb":
            p = ws / str(path)
            if not p.exists() or not p.is_file():
                return self._signal_result(severity), f"missing file {path}"
            max_kb = int(signal.get("max_kb", 1024))
            if p.stat().st_size > max_kb * 1024:
                return self._signal_result(severity), f"file too large {path}"
            return None
        return None

    def _c_manifest_health_signals(self):
        fails = []
        warns = []
        for aid, manifest in self.manifests.items():
            ws = self._agent_workspace(aid, WORKSPACE)
            for signal in manifest.get("health_signals", []):
                if not isinstance(signal, dict):
                    continue
                res = self._eval_health_signal(ws, signal)
                if not res:
                    continue
                status, details = res
                if status == "fail":
                    fails.append(f"{aid}:{details}")
                else:
                    warns.append(f"{aid}:{details}")
        if fails:
            return "fail", "; ".join(fails[:3]), None
        if warns:
            return "warn", "; ".join(warns[:3]), None
        return "pass", "manifest health signals ok", None

    def _c_cross_pollination_graph(self):
        missing = []
        for aid, manifest in self.manifests.items():
            ws = self._agent_workspace(aid, WORKSPACE)
            cp = manifest.get("cross_pollination")
            if not isinstance(cp, dict):
                continue
            for rel in cp.get("reads_from", []):
                p = self._expand_path(rel)
                if not p.is_absolute():
                    p = ws / str(rel)
                if not p.exists():
                    missing.append(f"{aid}:reads_from:{rel}")
            for rel in cp.get("writes_to", []):
                p = self._expand_path(rel)
                if not p.is_absolute():
                    p = ws / str(rel)
                parent = p if p.suffix == "" else p.parent
                if not parent.exists():
                    missing.append(f"{aid}:writes_to:{rel}")
        if missing:
            return "fail", f"cross-pollination paths missing: {', '.join(missing[:5])}", None
        return "pass", "cross-pollination graph valid", None

    def _c_agents_md_session_keys(self):
        text = self._safe_read_text(WORKSPACE / "AGENTS.md")
        # Dynamically check that each discovered agent has a session key in AGENTS.md
        missing = []
        for agent in self.agents:
            aid = agent["id"]
            if f"agent:{aid}:" not in text:
                missing.append(aid)
        if missing:
            return "fail", f"missing session key(s) for: {', '.join(missing)}", None
        return "pass", "session keys valid", None

    # 8) FILE SYSTEM & GIT
    def filesystem_git_checks(self, category: str) -> None:
        self.add_check(category, "disk_openclaw", self._c_disk_openclaw)
        self.add_check(category, "disk_workspace", self._c_disk_workspace)
        self.add_check(category, "disk_sessions", self._c_disk_sessions)
        self.add_check(category, "permissions_openclaw_dir", self._c_permissions_openclaw_dir)
        self.add_check(category, "permissions_config", self._c_permissions_config)
        self.add_check(category, "git_clean", self._c_git_clean)
        self.add_check(category, "git_backup_frequency", self._c_git_backup_frequency)
        self.add_check(category, "config_backup_fresh", self._c_config_backup_fresh)
        self.add_check(category, "large_files", self._c_large_files)
        self.add_check(category, "node_version", self._c_node_version)

    def _c_disk_openclaw(self):
        mb = self._dir_size_mb(OPENCLAW_DIR)
        if mb > 1024:
            return "warn", f"openclaw size high: {mb:.1f}MB", None
        return "pass", f"openclaw size {mb:.1f}MB", None

    def _c_disk_workspace(self):
        mb = self._dir_size_mb(WORKSPACE)
        self.stats["disk_workspace_mb"] = round(mb, 2)
        if mb > 500:
            return "warn", f"workspace size high: {mb:.1f}MB", None
        return "pass", f"workspace size {mb:.1f}MB", None

    def _c_disk_sessions(self):
        mb = self._dir_size_mb(OPENCLAW_DIR / "sessions")
        self.stats["disk_sessions_mb"] = round(mb, 2)
        if mb > 300:
            return "warn", f"sessions size high: {mb:.1f}MB", None
        return "pass", f"sessions size {mb:.1f}MB", None

    def _mode(self, path: Path) -> Optional[int]:
        try:
            return path.stat().st_mode & 0o777
        except Exception:
            return None

    def _c_permissions_openclaw_dir(self):
        mode = self._mode(OPENCLAW_DIR)
        if mode != 0o700:
            return "warn", f"mode is {oct(mode) if mode is not None else 'unknown'}, expected 0o700", None
        return "pass", "openclaw dir permissions correct", None

    def _c_permissions_config(self):
        mode = self._mode(CONFIG_PATH)
        if mode != 0o600:
            return "warn", f"mode is {oct(mode) if mode is not None else 'unknown'}, expected 0o600", None
        return "pass", "config permissions correct", None

    def _c_git_clean(self):
        res = self._run_cmd(["git", "status", "--porcelain"], cwd=WORKSPACE)
        if res["timeout"]:
            return "warn", "timeout", None
        if not res["ok"]:
            return "warn", f"git status failed: {res['stderr']}", None
        if res["stdout"].strip():
            return "warn", "workspace has uncommitted changes", None
        return "pass", "git clean", None

    def _c_git_backup_frequency(self):
        since = (self.now - timedelta(hours=24)).isoformat()
        res = self._run_cmd(["git", "rev-list", "--count", "--since", since, "HEAD"], cwd=WORKSPACE)
        if res["timeout"]:
            return "warn", "timeout", None
        if not res["ok"]:
            return "warn", f"git rev-list failed: {res['stderr']}", None
        try:
            count = int(res["stdout"].splitlines()[-1].strip())
        except Exception:
            count = 0
        self.stats["backup_commits_24h"] = count
        if count < 18:
            return "warn", f"backup commits in 24h low: {count}", None
        return "pass", f"backup commits in 24h: {count}", None

    def _c_config_backup_fresh(self):
        p = OPENCLAW_DIR / "openclaw.json.backup.latest"
        if not p.exists():
            return "warn", "backup config missing", None
        age = self.now - datetime.fromtimestamp(p.stat().st_mtime, tz=self.tz)
        if age > timedelta(hours=24):
            return "warn", f"backup older than 24h ({int(age.total_seconds()//3600)}h)", None
        return "pass", "config backup fresh", None

    def _c_large_files(self):
        large = []
        if WORKSPACE.exists():
            for p in WORKSPACE.glob("**/*"):
                if p.is_file() and p.stat().st_size > 5 * 1024 * 1024:
                    large.append(p.relative_to(WORKSPACE).as_posix())
        if large:
            return "warn", f"large files: {', '.join(large[:5])}", None
        return "pass", "no large files", None

    def _c_node_version(self):
        res = self._run_cmd(["node", "--version"])
        if res["timeout"]:
            return "warn", "timeout", None
        if not res["ok"]:
            return "warn", f"node check failed: {res['stderr']}", None
        ver = res["stdout"].strip()
        if not ver.startswith("v22"):
            return "warn", f"unexpected node version: {ver}", None
        return "pass", f"node version ok: {ver}", None

    # 9) AUTH & EXTERNAL SERVICES
    def auth_checks(self, category: str) -> None:
        self.add_check(category, "github_auth", self._c_github_auth)
        self.add_check(category, "gmail_auth", self._c_gmail_auth)
        self.add_check(category, "vercel_auth", self._c_vercel_auth)
        self.add_check(category, "qmd_binary_exists", self._c_qmd_binary_exists)
        self.add_check(category, "qmd_index_fresh", self._c_qmd_index_fresh)

    def _auth_check(self, cmd: List[str], env: Optional[Dict[str, str]] = None):
        final_env = os.environ.copy()
        if env:
            final_env.update(env)
        res = self._run_cmd(cmd, timeout=15, env=final_env)
        if res["timeout"]:
            return "warn", "timeout", None
        if not res["ok"]:
            return "warn", res["stderr"] or res["stdout"] or "command failed", None
        return "pass", "ok", None

    def _c_github_auth(self):
        return self._auth_check(["gh", "auth", "status"])

    def _c_gmail_auth(self):
        # Generic email auth check — adapt command and env vars to your email CLI
        # Example: gog, mutt, msmtp, or any CLI that can verify email access
        return self._auth_check(["echo", "no-email-cli-configured"])

    def _c_vercel_auth(self):
        return self._auth_check(["vercel", "whoami"])

    def _c_qmd_binary_exists(self):
        if not QMD_BINARY.exists() or not QMD_BINARY.is_file():
            return "warn", f"missing qmd binary at {QMD_BINARY}", None
        return "pass", "qmd binary exists", None

    def _c_qmd_index_fresh(self):
        if not QMD_BINARY.exists():
            return "warn", "qmd missing", None
        res = self._run_cmd([str(QMD_BINARY), "status"], timeout=15)
        if res["timeout"]:
            return "warn", "timeout", None
        if not res["ok"]:
            return "warn", res["stderr"] or "qmd status failed", None
        text = res["stdout"] + "\n" + res["stderr"]
        dt = None
        m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", text)
        if m:
            dt = self._parse_dt(m.group(1))
        else:
            m2 = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            if m2:
                dt = self._parse_dt(m2.group(1))
        if not dt:
            return "warn", "could not parse qmd updated time", None
        if self.now - dt > timedelta(hours=24):
            return "warn", "qmd index older than 24h", None
        return "pass", "qmd index fresh", None

    # 10) CONFIG & DOCUMENTATION SYNC
    def config_sync_checks(self, category: str) -> None:
        self.add_check(category, "config_valid_json", self._c_config_valid_json)
        self.add_check(category, "tools_md_cron_sync", self._c_tools_md_cron_sync)
        self.add_check(category, "tools_md_model_sync", self._c_tools_md_model_sync)
        self.add_check(category, "agents_md_cron_refs", self._c_agents_md_cron_refs)

    def _load_config(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not CONFIG_PATH.exists():
            return None, "config missing"
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data, None
        except Exception:
            pass
        res = self._run_cmd(["openclaw", "config", "get", "--json"], timeout=15)
        if res["ok"]:
            try:
                data = json.loads(res["stdout"])
                if isinstance(data, dict):
                    return data, None
            except Exception as exc:
                return None, f"fallback parse failed: {exc}"
        return None, "config parse failed"

    def _c_config_valid_json(self):
        _, err = self._load_config()
        if err:
            return "warn", err, None
        return "pass", "config valid", None

    def _c_tools_md_cron_sync(self):
        tools = self._safe_read_text(WORKSPACE / "TOOLS.md")
        crons, _ = self._with_crons()
        if not tools:
            return "warn", "TOOLS.md missing", None
        if not crons:
            return "warn", "cron unavailable", None
        names = [c.get("name") for c in crons if isinstance(c, dict)]
        missing = [n for n in names if n and n not in tools]
        if missing:
            return "warn", f"TOOLS.md missing cron refs: {', '.join(missing[:5])}", None
        return "pass", "TOOLS.md cron refs in sync", None

    def _c_tools_md_model_sync(self):
        tools = self._safe_read_text(WORKSPACE / "TOOLS.md")
        config, err = self._load_config()
        if not tools:
            return "warn", "TOOLS.md missing", None
        if err:
            return "warn", "config unavailable", None
        models = []
        if isinstance(config, dict):
            raw = config.get("models")
            if isinstance(raw, list):
                models = [str(m) for m in raw]
        missing = [m for m in models if m not in tools]
        if missing:
            return "warn", f"TOOLS.md missing model refs: {', '.join(missing[:5])}", None
        return "pass", "TOOLS.md model refs in sync", None

    def _c_agents_md_cron_refs(self):
        text = self._safe_read_text(WORKSPACE / "AGENTS.md")
        by = self._cron_by_name()
        if not text:
            return "warn", "AGENTS.md missing", None
        if not by.get("praeco-dream-cycle") or not by.get("dream-cycle"):
            return "warn", "dream cycle crons missing", None
        pa = by["praeco-dream-cycle"].get("schedule", "")
        lu = by["dream-cycle"].get("schedule", "")
        if str(pa) in text and str(lu) in text:
            return "pass", "AGENTS.md cron refs aligned", None
        if "02:15" in text and "03:00" in text and self._parse_hm(str(pa)) == (2, 15) and self._parse_hm(str(lu)) == (3, 0):
            return "pass", "AGENTS.md human-readable schedule aligned", None
        return "warn", "AGENTS.md dream cycle refs may be stale", None

    # 11) MORNING BRIEF PRE-CHECK
    def morning_brief_checks(self, category: str) -> None:
        self.add_check(category, "stale_blocker_detection", self._c_stale_blocker_detection)

    def _c_stale_blocker_detection(self):
        latest = self._latest_daily(MEMORY_DIR)
        if not latest:
            return "warn", "no daily note", None
        text = self._safe_read_text(latest)
        blocker_lines = [line.strip() for line in text.splitlines() if re.search(r"blocked|issue|reject|fail|broken|unresolved", line, re.I)]
        if not blocker_lines:
            return "pass", "no blockers in latest daily note", None
        warnings = []
        for line in blocker_lines:
            low = line.lower()
            for rec in self._load_items():
                ids = {f.get("id"): f for f in rec["data"] if isinstance(f, dict)}
                for fact in rec["data"]:
                    if not isinstance(fact, dict):
                        continue
                    if str(fact.get("fact", "")).lower() in low and fact.get("status") == "superseded" and fact.get("supersededBy") in ids:
                        warnings.append(f"Morning brief may report stale blocker: {fact.get('id')} superseded by {fact.get('supersededBy')}")
        if warnings:
            return "warn", warnings[0], None
        return "pass", "no stale blocker references detected", None


def main() -> int:
    auditor = VitalsAuditor()
    code, payload = auditor.run()
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # nosec
        fallback = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": 0,
            "summary": {"total_checks": 0, "pass": 0, "warn": 1, "fail": 0},
            "categories": {},
            "stats": {},
            "fatal_error": str(exc),
        }
        sys.stdout.write(json.dumps(fallback, ensure_ascii=False))
        sys.stdout.write("\n")
        raise SystemExit(1)
