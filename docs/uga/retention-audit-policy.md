---
status: DRAFT — for review by privacy office / counsel before reliance
last_edited: 2026-04-25
applies_to: remediate.jenkleiman.com (production deployment)
---

# Data Retention and Audit Logging Policy

This document records the data retention and audit-logging practices of the A11y Remediation tool deployed at `remediate.jenkleiman.com`. It is intended to support institutional privacy and security review (UGA EITS, privacy office, FERPA reviewers) and to give faculty users transparency into what is stored, for how long, and who can access it.

It is a working draft prepared by the project author (Jennifer Kleiman, doctoral student, Mary Frances Early College of Education). It is not a substitute for institutional counsel review and should be read alongside any executed Data Processing Agreement.

---

## 1. Scope

This policy covers data handled by the A11y Remediation tool, including:

- **Uploaded files:** course documents (.docx, .pdf, .pptx, .tex, .zip) submitted by authenticated faculty users.
- **Remediated outputs:** modified documents and accessibility reports produced by the pipeline.
- **Job records:** SQLite rows tracking each remediation request (filename, timestamps, status, cost, user owner).
- **User accounts:** email, display name, password hash (bcrypt), authentication provider, usage counters.
- **Audit logs:** structured application logs covering authentication, file operations, admin actions, and cost events.
- **Third-party subprocessor traffic:** API calls to Anthropic (Claude), Google (Gemini), and optionally OpenAI for the comprehension / strategy / execution / review pipeline.

Out of scope: data ingested by the user's own infrastructure (e.g., the user's email client) before submission, and data the user retrieves and stores after remediation.

## 2. Data Classification

The tool is designed for faculty-authored course materials — syllabi, lecture documents, slide decks, assignments — prepared for distribution to enrolled students. A document of this character that does not identify any individual student is not, by itself, an "education record" under FERPA.

However, this design intent does not by itself prevent inadvertent uploads of FERPA-protected content. The terms of use explicitly prohibit uploading material that would constitute an education record, including but not limited to:

- Graded student work or gradebooks
- Documents containing student names, IDs, or other personally identifiable information
- Faculty notes about specific students or accommodations
- Communications that quote or describe identifiable students

Users are responsible for ensuring uploaded material complies with this restriction. If institutional review of a particular workflow nonetheless determines that FERPA-protected data is being processed, the deletion-on-request process (Section 5) applies, and the relevant DPA terms govern.

## 3. Retention Periods

| Data class | Default retention | Mechanism | Rationale |
|---|---|---|---|
| Uploaded files (`data/uploads/`) | **30 days** from upload | Background cleanup loop (`src/web/retention.py`) | Long enough for reprocessing / faculty re-download; short enough to limit blast radius if compromised |
| Remediated outputs (`data/output/`) | **30 days** from completion | Same as above | Faculty are emailed when a job completes; 30 days gives generous window to retrieve |
| Job records (SQLite) | **18 months** from creation | Scheduled deletion (planned) + manual user/admin deletion | Sufficient for cost analytics, audit, and abuse investigation; bounded to limit cumulative metadata exposure (filenames may inadvertently contain informal PII despite Section 2 restrictions) |
| Audit logs (application logs) | **1 year** rolling | Hosting-platform log rotation | Sufficient for security incident investigation; aligned with typical institutional standards |
| User accounts | **Until user request** | User-initiated deletion (planned) or admin-initiated | Account closure on request; passive accounts retained pending user re-engagement |

Retention periods are configurable via environment variables (see `.env.template`). Changes take effect on the next cleanup cycle.

Files referenced by jobs in `queued` or `processing` state are never deleted regardless of age.

## 4. Access Controls

### 4.1 User-level isolation

All file paths, job records, and download URLs are scoped to the authenticated user's `user_id`. Job listing, status, download, and delete endpoints reject access to records owned by other users (including admins, by default — admins must use admin endpoints for cross-user access).

### 4.2 Admin access

A small number of admin accounts (controlled by the `ADMIN_EMAILS` environment variable) can:

- List all users and their usage stats
- View any user's job history
- Adjust per-user limits (max documents, max file size, page balance)
- Trigger ad-hoc retention cleanup
- View system-wide cost-cap status and toggle the kill switch

All admin actions are logged. Admin promotion is auditable through the application logs.

### 4.3 Authentication

- Local accounts use bcrypt-hashed passwords.
- OAuth (Google, Microsoft) flows do not store provider-side credentials; only the provider ID and the user's authenticated email are stored.
- Sessions are managed via httpOnly JWT cookies signed with `JWT_SECRET`.
- Password reset tokens are hashed (SHA-256) before storage, expire after a short window, and are single-use.

### 4.4 Network access

- The application runs behind Caddy as a reverse proxy.
- All authenticated endpoints require a valid session cookie; unauthenticated access is limited to the registration, login, password-reset, public health, and static-asset endpoints.
- Rate limits apply at multiple layers: per-IP (`_upload_limit`, `_login_limit`, etc.), per-user concurrent and hourly job caps, and a system-wide cost cap with kill switch.

## 5. Deletion Mechanisms

### 5.1 Automatic (retention cleanup)

The background cleanup loop runs every `RETENTION_INTERVAL_HOURS` (default 24h) and deletes files past their retention window. See Section 3 above and `src/web/retention.py` for implementation.

### 5.2 User-initiated

Users can delete individual jobs and bulk-delete via the web UI. Both actions remove the job's files from disk; the SQLite row is also removed.

In-flight jobs (`queued` or `processing`) cannot be deleted by the user; they must complete or fail first. This is to prevent inconsistent state mid-pipeline.

### 5.3 Account closure

A user-initiated account deletion endpoint is on the implementation roadmap. Until it ships, account closure is by request to the project author at `jennifer.kleiman@uga.edu`; a written confirmation will be issued after deletion.

### 5.4 Right to deletion (institutional users)

Deletion requests from authenticated institutional users — for individual jobs, account closure, or removal of specific records — are processed within **30 days** of receipt, with written confirmation issued upon completion. The 30-day window is a ceiling; routine job and account deletions complete substantially faster.

When an executed DPA is in place, the timeframes specified in that DPA govern and supersede this section if shorter. If the DPA imposes a shorter window, the tool will meet that window.

## 6. Audit Logging

### 6.1 What is logged

Application logs include, at INFO level by default:

- **Authentication events:** login (success / failure), logout, password reset request and use, OAuth callback. Failed login attempts are rate-limited and logged with IP.
- **File operations:** upload (size, page count, user, request ID), download (job ID, user, request ID), delete (job ID, user).
- **Job lifecycle:** queued, processing, phase transitions, completed, failed, recovered after restart. Each transition is timestamped.
- **Admin actions:** user creation, user updates, retention cleanup runs, cost-cap status reads, kill-switch state transitions.
- **Cost events:** API cost recorded per job, cost-cap rejections (with current spend vs cap).
- **System events:** startup, background-loop start/restart, structured-log configuration.

Logs do **not** include:

- Document content
- API request/response bodies (only token counts)
- Passwords (bcrypt hash never logged)
- Raw OAuth tokens
- Faculty grade data or student records (which the tool does not handle by design)

### 6.2 Request correlation

Every HTTP request is assigned a `X-Request-ID` (UUID4 or trusted upstream value from Caddy). The ID is threaded through every log record produced during that request, allowing operators to grep a single user's interaction across the system.

### 6.3 Log retention and access

- Logs are written to standard output and captured by `journalctl` on the production host.
- Default journald rotation is 1 year or whichever is reached first of the configured `SystemMaxUse` and `SystemMaxFileSize`.
- Log access is restricted to administrators with shell access to the production host.
- Logs are not exported to third-party logging services without explicit institutional approval.

## 7. Subprocessor Data Flow

The pipeline calls third-party APIs to perform comprehension, strategy, execution, and review:

| Subprocessor | Used for | Data sent | Data retention by subprocessor |
|---|---|---|---|
| Anthropic (Claude) | Strategy, execution reasoning, review | Document text + metadata, derived prompts | Per Anthropic's Commercial Terms of Service; zero-retention option available on the API |
| Google (Gemini) | Comprehension, page-level visual analysis, OCR | Document text and rendered page images | Per Google's API terms; data-handling settings configurable per project |
| OpenAI (optional) | Fallback / specific subtasks | Same character as above | Per OpenAI's Enterprise terms when used |

### 7.1 Current state

The tool currently calls these subprocessors under their standard commercial terms. No data residency, retention, or sub-processor sub-contracting commitments beyond those terms apply to the open free-tier deployment.

### 7.2 Institutional deployment commitments

For institutional pilots and any workflow where FERPA-protected or otherwise sensitive data may be processed, an executed Data Processing Agreement (DPA) with the institution is a prerequisite, and the following are committed:

- **Data residency:** subprocessor processing will be configured for U.S. region only.
- **Zero-retention:** zero-retention or shortest-available retention options will be enabled on each API where the subprocessor offers them.
- **Subprocessor changes:** no new subprocessor will be engaged for institutional workloads without prior written notice (minimum 30 days) and the institution's right to object before the change takes effect.
- **Sub-processor sub-contracting:** the list of approved sub-processors will be provided to the institution and updated under the same notice-and-objection process as direct subprocessors.
- **Breach notification:** the institution will be notified of any subprocessor breach affecting institutional data without undue delay, and in no case later than 24 hours after the project author becomes aware. The project author will share whatever the subprocessor's own breach notification provides.
- **Right to audit:** the institution retains audit rights as specified in the executed DPA. Where direct audit is impractical, the project author will share subprocessor SOC 2 reports or equivalent third-party attestations on request.

These commitments are baseline; an institution's executed DPA may impose additional terms that supersede this section.

## 8. Incident Response

In the event of a suspected security or privacy incident:

1. **Containment:** the cost-cap kill switch is engaged, halting all new uploads.
2. **Institutional notification (priority):** the institution's designated security and privacy contacts (e.g., UGA EITS Information Security Officer, University Privacy Officer) are notified without undue delay, and in no case later than **24 hours** after the project author becomes aware of the incident. This notification precedes any user-facing communication, and the project author will coordinate with the institution on the content and timing of any user notification.
3. **Investigation:** the project author investigates application logs scoped to the affected window, identifies affected users and data, and produces a preliminary impact assessment.
4. **User notification:** affected users are notified after coordination with the institution, generally within 72 hours of incident confirmation. The institution's privacy office may direct earlier or different notification depending on the nature of the incident.
5. **Subprocessor coordination:** if a subprocessor is involved, breach notifications received from that subprocessor are forwarded to the institution promptly.
6. **Written incident report** is produced and archived, including timeline, root cause, scope of affected data, mitigation taken, and recommendations.

"24 hours" and "72 hours" are measured from the moment the project author has confirmed an incident has occurred (i.e., past initial triage). The institution's executed DPA or institutional policy may impose tighter windows, in which case those govern.

This is a baseline; institutions with formal incident-response requirements will see this section adapted to align with their playbook before pilot launch.

## 9. Review Schedule

This policy is reviewed:

- On material change to the application (new subprocessor, new data class, new endpoint exposing user data)
- At minimum every 12 months
- On request from a participating institution's privacy office

The most recent review date is at the top of this document.

## 10. Configuration Inventory

For operators and reviewers, the following environment variables control retention and audit behavior. See `.env.template` for the canonical list.

| Variable | Default | Effect |
|---|---|---|
| `RETENTION_ENABLED` | enabled | `0`/`false`/`no` disables the cleanup loop entirely |
| `RETENTION_DAYS_UPLOADS` | 30 | Age threshold for `data/uploads/` deletion |
| `RETENTION_DAYS_OUTPUT` | 30 | Age threshold for `data/output/` deletion |
| `RETENTION_INTERVAL_HOURS` | 24 | How often the cleanup loop runs |
| `MAX_USER_CONCURRENT_JOBS` | 5 | Max in-flight jobs per non-admin user |
| `MAX_USER_JOBS_PER_HOUR` | 30 | Max submissions in trailing hour per non-admin user |
| `MAX_CONCURRENT_JOBS` | 1 | System-wide concurrent job ceiling |
| `COST_CAP_DAILY_USD` | unlimited | Daily $ ceiling on cumulative API spend |
| `COST_CAP_WEEKLY_USD` | unlimited | 7-day rolling $ ceiling |
| `COST_CAP_KILL_SWITCH` | off | Hard switch to reject all uploads (e.g., during incident) |
| `ADMIN_EMAILS` | (set per deployment) | Comma-separated emails auto-promoted to admin |

---

## Document maintenance notes (not part of policy)

- This draft was reviewed by Gemini 2.5 Pro on 2026-04-25 for red-flag issues a US R1 university privacy office would surface. Five concrete revisions applied: tightened FERPA framing (Section 2 now prohibits rather than disclaims), bounded job-record retention at 18 months (was indefinite), expanded subprocessor section with US data residency / breach-notification / audit-rights commitments (Section 7), prioritized institutional-first incident notification at 24h (Section 8), and removed "best-effort" language from right-to-deletion (Section 5.4). One structural suggestion (split into formal policy vs operational guide) was deliberately deferred until first institutional review tells us which sections they actually rely on.
- The 18-month job-record retention requires a scheduled deletion job that doesn't yet exist. Either ship that before the policy goes live, or commit a manual quarterly deletion-by-script process and document it here as the interim mechanism.
- The "user-initiated account deletion" endpoint referenced in Section 5.3 is on the roadmap, not yet shipped. Update this doc when it lands.
- A structured Data Processing Agreement template (separate from this internal policy) should accompany institutional pilots.
- Section 7 deliberately does not name pricing tiers or specific retention durations for subprocessors — those move and should be looked up at execution time, not memorized here.
- Counsel review is still required before this can be cited in a contractual context. The author is a doctoral student, not a lawyer.
