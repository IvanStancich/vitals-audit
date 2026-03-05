# vitals-audit

Infrastructure health monitoring skill for [OpenClaw](https://github.com/openclaw/openclaw). Runs 77 deterministic checks across your entire OpenClaw system in ~4 seconds, then uses an LLM to interpret results and surface what actually matters.

## What it checks

| Category | Checks | Examples |
|---|---|---|
| Memory integrity | 13 | Duplicate IDs, broken supersede chains, schema validation |
| Knowledge graph quality | 7 | Stale summaries, relationship islands, archive candidates |
| Daily notes & memory | 5 | Note gaps, cross-pollination, MEMORY.md guard violations |
| Tasks audit | 4 | Stale deadlines, zombie tasks, missing descriptions |
| Cron health | 11 | Consecutive errors, timezone consistency, stale runs |
| Skills validation | 7 | Missing SKILL.md, frontmatter issues, broken symlinks |
| Cross-agent consistency | 8 | Workspace health, content pipeline, manifest checks |
| Filesystem & git | 10 | Disk usage, permissions, large files, backup frequency |
| Auth & external services | 5 | GitHub, Gmail, Vercel, QMD index |
| Config & docs sync | 3 | TOOLS.md vs actual crons, AGENTS.md refs |
| Morning brief pre-check | 1 | Detects stale blockers before they hit the brief |
| Manifest checks | 4 | Required files/dirs, health signals, cross-pollination graph |

## Architecture

```
vitals-check.py (pure Python, ~4 seconds, zero API cost)
        ↓ structured JSON
  LLM interpreter (Opus/Sonnet, ~$0.20-0.30/run)
        ↓ analysis
  Notification channel (Telegram, Discord, etc.)
```

The script is deterministic and read-only. It never modifies files. The LLM adds pattern recognition and trend analysis on top.

## Auto-discovery

The script automatically discovers:
- **Agents** from `openclaw.json` — add an agent, it's monitored
- **Crons** from live state — validates all enabled crons by interval
- **Entities** from filesystem — scans all `items.json` files
- **Custom checks** from per-agent `vitals.json` manifests (optional)

Zero central config to maintain.

## Setup

### 1. Install the skill

Copy the skill folder to your OpenClaw skills directory:

```bash
# Global (all agents see it)
cp -r vitals-audit ~/.openclaw/skills/

# Or workspace-specific
cp -r vitals-audit ~/.openclaw/workspace/skills/
```

### 2. Place the script

```bash
mkdir -p ~/.openclaw/workspace/scripts/vitals
cp vitals-check.py run-vitals.sh ~/.openclaw/workspace/scripts/vitals/
chmod +x ~/.openclaw/workspace/scripts/vitals/run-vitals.sh
```

### 3. Create a daily cron

```bash
openclaw cron add --name daily-vitals-check \
  --schedule '{"kind":"cron","expr":"30 6 * * *","tz":"YOUR/TIMEZONE"}' \
  --payload '{"kind":"agentTurn","message":"Run the daily vitals audit. Read the vitals-audit skill and follow it exactly.","model":"anthropic/claude-opus-4-6","timeoutSeconds":600}' \
  --sessionTarget isolated
```

### 4. (Optional) Add per-agent manifests

Drop a `vitals.json` in any agent's workspace for custom health checks:

```json
{
  "$schema": "vitals-manifest-v1",
  "agent": "my-agent",
  "required_files": ["BRAND_VOICE.md"],
  "required_dirs": ["memory/content-log"],
  "cross_pollination": {
    "reads_from": ["~/.openclaw/workspace/memory"]
  },
  "health_signals": [
    {
      "type": "file_freshness",
      "path": "memory/strategy/*.md",
      "max_stale_days": 30,
      "severity": "warn"
    }
  ]
}
```

## Fix mode

The skill has two modes:
- **AUDIT** (daily cron): run checks → interpret → report
- **FIX** (on demand): read report → verify each issue → fix safe ones → log to `fix-log.json`

Fix mode enforces: **analyze everything before fixing, never guess.** Every fix must be verified, understood, targeted, and confirmed. Wrong fixes are worse than no fix.

## Health signal types

| Type | What it checks | Parameters |
|---|---|---|
| `file_freshness` | File mtime within threshold | `path` (glob ok), `max_stale_days` |
| `dir_activity` | Recent file modifications in dir | `path`, `max_gap_days` |
| `file_contains` | File matches regex pattern | `path`, `pattern` |
| `file_max_size_kb` | File under size limit | `path`, `max_kb` |

## Requirements

- Python 3.11+ (stdlib only, no pip dependencies)
- OpenClaw with at least one configured agent
- Optional: `gh`, `gog`, `vercel`, `qmd` CLIs for auth checks

## License

MIT
