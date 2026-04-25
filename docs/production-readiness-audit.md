# Production-Readiness Audit — 2026-04-25

Triggered by a major university inquiry. Inventories the gaps between the
current architecture (research / single-user / free-tier) and what's
required for institutional deployment. Severity: **R** = blocks deal,
**Y** = needed before scale, **G** = nice-to-have / iterate after launch.

## Capacity & concurrency

### R1 — Server processes one document at a time, globally

`src/web/app.py:98` hardcodes `_processing_semaphore = threading.Semaphore(1)`. Regardless of how many faculty submit simultaneously, the server processes one document end-to-end (~30-180s) before starting the next. **At semester start when 50 faculty upload syllabi within an hour, the 50th faculty waits 25-150 minutes.**

The comment justifies this: *"With a 30k input tokens/min Claude rate limit, only one job can safely process at a time."* — that constraint is real but solvable by raising Anthropic tier (Tier 2: 1000 RPM, 300K input/min for Sonnet on confirmed deposit). At Tier 3, can raise semaphore to 4-8.

**Fix path:** External job queue (dramatiq + Redis, or arq + Postgres). Worker pool sized to API tier limits. Frontend shows queue position.

**Effort:** 1-2 weeks. Required before deal.

### R2 — Threads die with the process; recovery is fragile

`src/web/app.py:520` uses `threading.Thread(daemon=True)`. If uvicorn restarts (deploy, OOM, crash), all in-flight jobs die mid-pipeline. `_recover_stuck_jobs()` (lines 113-148) resets `processing` → `queued` and restarts threads, so they DO get picked back up — but the work restarts from scratch (no checkpoint), the user sees a phase reset, and any partial output is wasted.

Worse: if a worker thread dies mid-job (uncaught exception escaping the try/except, OOM kill of the thread, etc.) without the process restarting, the row stays in `processing` until the next restart. No active monitoring.

**Fix path:** Real queue (R1) handles persistence and retries automatically. Add per-phase checkpointing to avoid re-running expensive Gemini calls on restart.

### Y1 — SQLite write contention at multi-user load

`src/web/jobs.py:26` opens thread-local SQLite connections with WAL mode. WAL helps but multi-tenant writes (job updates from worker, status reads from web, billing deductions, user logins) will contend. SQLite's lock granularity is per-database. At ~5 concurrent writers performance falls off cliff.

**Fix path:** Migrate to Postgres. Schema is small (jobs + users + billing). 1-2 days work + deploy.

**Effort:** 2-3 days. Before signing.

### Y2 — Single Oracle Cloud ARM instance, 2 OCPU / 12GB

veraPDF, iText, LaTeXML are CPU-bound subprocesses. WeasyPrint is RAM-hungry (Pango, Cairo). Right-sized for one concurrent job; falls over at 4+. With horizontal-scale architecture (R1 fix), can run multiple workers — but each needs ~2GB peak. 12GB box ≈ 4 workers max.

**Fix path:**
- Short-term: Upgrade to Oracle Cloud paid (4 OCPU / 24GB ARM is ~$30/mo). Probably enough for a single uni.
- Medium-term: Deploy worker pool on a second VM, web on first. Or move to Kubernetes / managed service.

**Effort:** 1 day to upgrade; 1 week to split web/worker; weeks to k8s.

## API rate limits

### R3 — Single shared Gemini + Anthropic key for all users

`GEMINI_API_KEY` and `ANTHROPIC_API_KEY` are global. All users share the same per-key tier limits. Today (2026-04-25) we hit Gemini's free-tier 20 RPD because identity verification was pending — bit us in dev, would catastrophically bit us in prod.

For a uni (estimate: 100-500 docs/week peak, ~3 calls/doc avg), we need:
- **Gemini**: At minimum Tier 1 (1000 RPM, no daily cap). Verification *must* be cleared before launch. Tier 2 ($250 spent + 30 days) gives more headroom and is cheap insurance.
- **Anthropic**: Tier 3 (>$400 deposited, ~5000 RPM, 800K input/min for Sonnet). Without strategy LLM call, our usage drops, but execute phase still needs it.

**Fix path:**
- Confirm Gemini verification cleared before sales conversation continues.
- Pre-deposit at both vendors to bump tier ahead of launch.
- Add cost monitoring + budget alerts.

**Effort:** 1 day (account upgrades + monitoring).

### Y3 — Per-org quotas don't exist

`src/web/users.py` has `pages_balance` per user. No "this university gets N docs/semester" model. A uni would want:
- Pooled budget across all faculty in their domain
- Per-faculty caps within that pool
- Admin dashboard: who's used what
- Billing event when balance crosses thresholds

**Fix path:** Add `Organization` table; users belong to org; org has pages_balance; per-user soft caps within org. New admin UI.

**Effort:** 1-2 weeks. Before signing or as condition of signing.

## Data & compliance

### R4 — No FERPA story

Faculty documents likely contain student PII: names in graded examples, in case studies, in roster excerpts. Currently we send the entire document to:
- Google Gemini (for vision + comprehension)
- Anthropic Claude (for execute phase, possibly review)

A university won't sign without:
- Signed Data Processing Addenda with Anthropic and Google (both vendors offer them; Anthropic has FERPA-compatible terms; Google has them via Workspace BAA but Gemini API may need separate review)
- Documented data retention policy (we currently keep uploads + outputs in `data/uploads/` and `data/output/` indefinitely)
- Audit logging (who accessed what, when)
- Data residency claims (Google + Anthropic both run multi-region; uni IT will ask)
- Deletion-on-request workflow

**Fix path:** Sign DPAs (legal time, weeks). Add 30-day or 7-day retention with auto-cleanup job. Add audit log table. Document a privacy policy specific to this tool.

**Effort:** 2-4 weeks (mostly legal + policy, not code). Hard requirement for deal.

### R5 — No SAML / SSO via uni IdP

Currently: username+password, plus Google and Microsoft personal OAuth. Universities almost universally require SAML or OIDC integration with their IdP (Shibboleth, Azure AD, Okta) so faculty can use existing credentials and IT can deprovision automatically.

**Fix path:** Add SAML support via `python-saml3` or OIDC support extending existing OAuth. Most unis want SAML. Each integration takes ~3-5 days (cert exchange, attribute mapping, test environment).

**Effort:** 1 week per uni for first integration; faster after that.

### Y4 — No data lifecycle policy

Files in `data/uploads/` and `data/output/` accumulate forever. Disk fills. No mechanism to honor "delete my data" requests beyond manual file removal.

**Fix path:** Add cleanup job (cron-style) that deletes files older than N days, marking jobs as "expired" but keeping metadata. Add "delete all my data" button for users.

**Effort:** 2-3 days.

## Operations

### Y5 — No cost cap / kill switch

A misbehaving worker, recursive job, or determined abuser could spike API spend. We have no monthly budget alert, no per-org cap, no automatic shutoff.

**Fix path:** Track spend per request in `cost_summary` (already done in pipeline). Add daily/monthly aggregate per org. Alert when 80% of budget. Hard cap when 100%.

**Effort:** 3-5 days.

### Y6 — Limited observability

Have: structured logging via `logger` calls; written to journald via systemd. Don't have: metrics, distributed tracing, alerting, dashboards.

**Fix path:** Prometheus exporter + Grafana, or similar managed tool. Alert on: error rate >5%, queue depth >100, API tier limit approach, disk >80%.

**Effort:** 1 week.

### Y7 — Recovery + rollback story is thin

`_recover_stuck_jobs()` exists but (per R2) doesn't actually re-run anything. No "abort job", no "retry failed job" UI. Deploys aren't rolling.

**Fix path:** Real queue (R1) gets us most of this. Add UI buttons. Document blue/green deploy.

**Effort:** Folded into R1.

## Load testing

We have NO load test today. Production-readiness audit recommends:
1. Run synthetic load: N=1, 5, 20, 50 simultaneous uploads. Measure: response latency, queue depth, error rate, API tier exhaustion, server CPU/memory.
2. Identify the breaking point.
3. Fix the first thing that breaks. Repeat.

The breaking point today is **N=2** (Semaphore(1) gates everything; second upload waits for first to complete). After R1 fix and Anthropic Tier 2: probably N=8-12 before API rate-limited. After Postgres + paid Oracle Cloud: probably N=30-50.

**Fix path:** Locust or k6 script that uploads sample PDFs concurrently, varies N, plots metrics.

**Effort:** 2-3 days.

## Recommended phasing

### Before continuing the sales conversation
- **R3** (Gemini verification cleared, paid tiers confirmed at both vendors) — 1 day
- **R4 part 1** (DPAs initiated with Anthropic + Google) — start the clock now, takes weeks
- **R5** (SAML support roadmap, sized) — be able to commit to a timeline

### Before signing
- **R1, R2** (real job queue, recovery) — 1-2 weeks
- **Y1** (Postgres) — 2-3 days
- **R4 part 2** (DPAs signed, retention policy documented) — depends on legal
- **R5** (one working SAML integration with a willing test partner) — 1 week
- **Y2** (paid Oracle Cloud upgrade) — 1 day
- Load test confirming N=20 simultaneous uploads handles cleanly — 2-3 days

### Before launch
- **Y3** (per-org quotas) — 1-2 weeks
- **Y4** (data retention) — 2-3 days
- **Y5, Y6** (cost cap + observability) — 1-2 weeks
- Full load test through to N=100 — 1 week

### Total
**~6-10 weeks** of engineering work, plus parallel legal time on DPAs that's outside our control. A solo developer can do the engineering; legal needs Jennifer + the university's procurement office.

## What's not in scope of this audit
- The remediation pipeline itself (separately measured at 86.1% veraPDF reduction in v5)
- Pricing model / per-page vs per-doc billing
- Support model (who answers tickets at 2am)
- Marketing / landing page / contracts boilerplate

These need separate planning.
