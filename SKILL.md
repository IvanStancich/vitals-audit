---
name: vitals-audit
description: Infrastructure vitals audit and fix skill — run 73 deterministic health checks across the entire OpenClaw system, interpret results, and fix verified issues with logged remediation. Use when running scheduled vitals checks, asked to "fix the vitals issues", "fix the health report", "address the audit findings", "check system health", or "audit the infrastructure". Two modes: AUDIT (read-only, report) and FIX (analyze, fix verified issues, log everything).
---

# Vitals Audit & Fix

Two modes:
- **AUDIT**: Run the script, interpret results, report.
- **FIX**: Read the latest report, verify each issue, fix what's safe, log everything.

Determine mode from context: if asked to *check* or *audit* → AUDIT mode. If asked to *fix* or *address issues* → FIX mode.

---

## AUDIT MODE

### Step 1: Dump cron state
Call the cron tool with `action: "list"` and `includeDisabled: true`. Then use the **write tool** (not exec) to save the complete JSON response to `/tmp/vitals-cron-state.json`. Write the raw JSON exactly as received — do not reformat or summarize it.

### Step 2: Run the script
```bash
bash ~/.openclaw/workspace/scripts/vitals/run-vitals.sh
```
Runs 73 checks in ~4 seconds. Saves raw JSON to `memory/audits/YYYY-MM-DD-vitals.json`. Prints JSON for interpretation.

### Step 3: Interpret results

**Pattern recognition** — Don't list failures. Ask WHY:
- Multiple `relationship_islands` → dream cycle isn't linking entities when writing facts
- `stale_active_blockers` → morning brief will report resolved issues as open (the March 5 bug)
- `cold_fact_ratio` high on active projects → dream cycle isn't bumping access counts
- Auth failures → crons depending on that service will silently break
- `cross_references_valid` failures → facts reference entities that were never created
- `summary_freshness` stale → weekly-summary-regen skipped or broken
- `timezone_consistency` failures → potential race conditions between crons

**Severity assessment** — Script flags mechanically. You decide what matters:
- Warn on archived/low-priority project? Usually fine — note it.
- Auth failure? Effectively critical even if flagged as warn.
- `timezone_consistency`? High priority — caused the March 5 race condition.
- `duplicate_ids`? Critical — breaks supersede chains and fact retrieval.

**Trend comparison** — Find most recent previous `*-vitals.json` in `memory/audits/`:
- `total_facts` growing = healthy; stagnant = dream cycle may not be capturing
- `hot/warm/cold` ratio: cold growing = decay working OR low activity
- `disk_workspace_mb` sudden jump = investigate
- `backup_commits_24h` drop = backup cron issue
- Check `fix-log.json` for recurring issues: if same check failed before and "prevention" was noted but not implemented, flag it

**Recommendations** — For each real issue: WHO (which agent or user), WHAT (specific action), PRIORITY.

### Step 4: Save report
- `memory/audits/YYYY-MM-DD-vitals.md` — interpreted analysis

### Step 5: Notify
- ALL pass → `NO_REPLY`
- Warnings only → max 200 words to #status (your configured #status channel)
- ANY failures → to #status AND short alert to #general (your configured #general channel)

Use ✅⚠️❌ emojis. Be scannable.

---

## FIX MODE

### ⚠️ CORE RULE: ANALYZE BEFORE FIXING — NEVER GUESS

A wrong fix is worse than no fix. Every fix must be:
1. **Verified**: read the actual file/config/state before touching it
2. **Understood**: know exactly what is broken and why before changing anything
3. **Targeted**: change only the specific broken thing, nothing else
4. **Confirmed**: re-run the relevant check after fixing to verify it resolved

If you are not 100% certain what caused an issue and what the correct fix is — **stop and ask the user**. Do not pattern-match against similar-looking errors from the past. Do not infer. Do not guess.

### Step 1: Load the report
Read the most recent `memory/audits/YYYY-MM-DD-vitals.json` (raw check results) AND `YYYY-MM-DD-vitals.md` (interpretation). Also read `memory/audits/fix-log.json` if it exists.

### Step 2: For each non-passing check
For every FAIL and WARN, work through this decision tree **one issue at a time**:

**A. Is this a by-design/known exception?**
Examples: name conflicts from intentional symlinks, Praeco notes before cycle start date, websmith non-standard frontmatter.
→ If yes: skip, no action needed.

**B. Is this self-healing?**
Examples: summary_freshness and summary_items_drift (fixed by Sunday's weekly-summary-regen), git_clean (fixed by hourly backup).
→ If yes: note it in the report, skip.

**C. Can I verify the exact cause by reading files?**
→ Read the relevant file(s) right now. Confirm the exact error.
→ If you cannot confirm the exact cause: stop, ask the user.

**D. Is the fix safe and reversible?**
Safe = read-only or purely additive (adding a missing file, correcting a wrong value).
Not safe = deleting data, restructuring entities, modifying scripts.
→ If not safe: ask the user before proceeding.

**E. Apply the fix.**
Make the minimal change needed. Nothing extra.

**F. Verify the fix.**
Re-run the specific check (or the full script) and confirm the issue is gone.

**G. Log the fix** (see Fix Log section below).

### Step 3: Re-run the script
After all fixes, run `bash run-vitals.sh` again. Compare before/after counts. The post-fix run output is your verification.

### Step 4: Report to user
Send a summary to Telegram (your configured notification channel):
- What was fixed (specific issues, not vague categories)
- What was skipped and why (self-healing, by-design, or "needs your decision")
- Anything that needs user input
- Before/after check counts

---

## FIX LOG (`memory/audits/fix-log.json`)

Every fix applied in FIX mode must be logged. This log builds up over time to reveal patterns — what keeps breaking and why — so we can fix root causes, not just symptoms.

**Log entry schema:**
```json
{
  "id": "fix-NNN",
  "date": "YYYY-MM-DD",
  "check": "check_name_from_script",
  "entity": "optional: which entity/file was affected",
  "error_detail": "exact error message from vitals script",
  "root_cause": "why did this happen? be specific",
  "fix_applied": "exactly what was changed, including file paths",
  "verification": "what check result confirmed the fix worked",
  "prevention": "what change to a prompt/process would prevent recurrence",
  "recurred": false
}
```

**Rules:**
- `id` must be sequential — always read the log first to find the next ID
- `root_cause` must be specific. "broken cross-reference" is not a root cause. "dream cycle wrote relatedEntities without verifying entity dir exists" is.
- `prevention` must be actionable. "be more careful" is not a prevention. "add entity existence check to dream cycle prompt step 3" is.
- `recurred` stays false on first occurrence. If you see the same `check` + `entity` combination appear again in a later audit, find the original log entry and set `recurred: true`, then add a new entry explaining what the persistent root cause is.
- Never delete entries — only add to the log.

**Recurrence detection in AUDIT mode:**
When interpreting vitals results, always read `fix-log.json` and check:
- Has this check failed before for this same entity?
- Was a prevention measure noted? Was it ever implemented?
- If the same issue has occurred 3+ times: escalate — flag it as a systemic problem, not a one-off.

---

## Troubleshooting

- **Cron checks all "unavailable"**: Step 1 failed — `/tmp/vitals-cron-state.json` missing or malformed. Verify it exists and contains valid JSON with a `jobs` array at `data["jobs"]`.
- **Script crashes**: Report the full traceback. Do NOT attempt to fix the script inline. File it as a known issue.
- **Auth checks timeout**: Normal if service is slow (15s timeout per check). Flag as warn.
- **"Could not parse" warnings**: Likely a stale cron state file. Dump fresh state and re-run.
- **Fix looks right but check still fails**: Stop. Something about your understanding of the fix is wrong. Re-read the relevant files and ask the user.
