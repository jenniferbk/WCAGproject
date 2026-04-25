---
status: DRAFT — verify all bracketed placeholders before sending
recipient: UGA EITS Identity Management
purpose: Request SAML 2.0 / Shibboleth Service Provider registration for remediate.jenkleiman.com
last_edited: 2026-04-25
---

# Draft: SAML SP registration request — UGA EITS

## Pre-send checklist

- [x] EITS contact verified: `idm@uga.edu` (per https://uga.teamdynamix.com/TDClient/3190/eitsclientportal/KB/ArticleDet?ID=160724)
- [x] Title and college: Doctoral student, Mary Frances Early College of Education
- [x] Originating UGA contact: Dean Denise Spangler (Mary Frances Early College of Education)
- [ ] **Confirm with Dean Spangler before sending** — she should know this letter is going out, and I recommend CCing her so EITS sees the dean is engaged
- [ ] Confirm preprint posting status — adjust "preprint forthcoming" wording if posted by send date
- [ ] Decide whether to attach SP metadata XML now (need to build SAML SP first) or commit to delivering it on a stated timeline
- [ ] Confirm target term for pilot — Summer 2026? Fall 2026?
- [ ] Confirm whether to also request **UGASSO** registration in the same email (EITS KB article 160724 recommends important applications be enrolled in UGASSO in addition to the Federated IdP, for ongoing support if the federation provider has issues)

---

## Subject line options

- Request: Shibboleth SP registration for accessibility remediation tool serving UGA faculty
- SAML SP onboarding request — accessibility remediation pilot for UGA instructional faculty

---

## Body

To: idm@uga.edu
From: Jennifer Kleiman <jennifer.kleiman@uga.edu>
CC: Dean Denise Spangler `[VERIFY: confirm with her before sending; use her preferred address]`
Subject: Request to register a third-party application with UGA's Federated Identity Provider

Hello,

I'm Jennifer Kleiman, a doctoral student in the Mary Frances Early College of Education at UGA. Following the process described in EITS knowledge base article 160724, I'm writing to request registration of a third-party web application with UGA's Federated Identity Provider so that UGA faculty can access it using their MyID credentials.

The application is an accessibility remediation tool I've developed for university course materials, currently running at https://remediate.jenkleiman.com/. Dean Denise Spangler has expressed interest in piloting it for instructional faculty in the Mary Frances Early College of Education, and that pilot is what's prompting this request. I'd also like to discuss enrolling the application in UGASSO at the same time, per the article's recommendation that important applications use both for resilience.

### Background

The tool helps faculty bring course documents (.docx, .pdf, .pptx) into WCAG 2.1 AA compliance — relevant to the DOJ Title II ADA digital accessibility rule for public colleges and universities. (DOJ published an Interim Final Rule on April 20, 2026 extending the compliance date for entities with population ≥50,000 from April 24, 2026 to April 26, 2027; the underlying obligation is unchanged, and a controlled rollout starting now is preferable to a rush in early 2027.) The tool uses an agentic AI pipeline (comprehend → strategize → execute → review) that has been measured against the Kumar et al. PDF accessibility benchmark and currently achieves an 86.1% PDF/UA violation reduction across the benchmark documents (6,227 → 865 veraPDF-verified violations, 0 regressions). A preprint is forthcoming; the work is in collaboration with the original benchmark authors at AI2.

The deployed instance currently uses local accounts and Google/Microsoft OAuth. To onboard UGA faculty with their existing MyID credentials — and to keep us within UGA's identity governance for the pilot — I'd like to register as a Shibboleth SP.

### What I'm requesting

1. Registration of `https://remediate.jenkleiman.com/` as a SAML 2.0 Service Provider in UGA's Federated Identity Provider, with attribute release scoped to the minimum needed for authentication and account creation.
2. Guidance on the parallel UGASSO enrollment process so the application is supported through both, as recommended in EITS KB article 160724.

### What I can provide

- **SP metadata XML** — I'll generate this to your specifications. Proposed entity ID `https://remediate.jenkleiman.com/saml/metadata`, ACS URL `https://remediate.jenkleiman.com/saml/acs`, with signing and encryption certificates. (Note: the SAML SP code is on the implementation roadmap; I can deliver SP metadata within `[VERIFY: timeline — suggest 2–3 weeks]` of receiving onboarding requirements.)
- **Attribute requirements (minimal):**
    - `eduPersonPrincipalName` — for unique account identifier
    - `mail` — for account email
    - `displayName` (or `givenName` + `sn`) — for UI personalization
- No FERPA-protected attributes are needed. No directory information beyond the above.
- **Technical contact:** Jennifer Kleiman, jennifer.kleiman@uga.edu, `[VERIFY: phone if you want one listed]`
- **Security contact:** same as above for the pilot phase
- **Privacy / data handling summary** (full policy attached or available on request — see "Data handling notes" below)

### What I'd need from EITS

- IdP entity ID and metadata URL — please confirm the production endpoint
- Confirmation of supported SAML profiles (SAML 2.0 Web Browser SSO, expected)
- Attribute release policy and approval workflow
- Any test/staging IdP available for SP integration testing before production cutover
- Your standard SP onboarding documentation, if available
- Any UGA-specific requirements for SP metadata (signing algorithm preferences, NameID format, AuthnContext requirements, MFA expectations)
- UGASSO enrollment requirements and how those interact with the federated SP registration

### Data handling notes (for the FERPA / privacy conversation)

The tool processes course materials — syllabi, lecture documents, assignments, slide decks — that faculty upload voluntarily for remediation. To my reading, these are faculty-authored materials prepared for distribution to enrolled students and are not student records under FERPA, but I want to flag the data flow honestly so EITS / the privacy office can assess:

- **What's processed:** Faculty-authored course documents. Faculty are instructed not to upload graded work or documents containing identifiable student information.
- **AI pipeline:** The pipeline calls Anthropic's Claude API and Google's Gemini API for document comprehension, remediation strategy, and review. Both vendors offer enterprise DPAs and zero-retention API options that can be enabled as part of UGA onboarding.
- **Storage:** Uploads and remediated outputs are stored on the application server with a 30-day retention default; older files are removed automatically. A formal retention and audit-logging policy has been drafted (`docs/uga/retention-audit-policy.md` in the project repo) and is available to share with the privacy office for review.
- **Audit logging:** Per-user submission and retrieval logs are maintained for security and abuse detection.
- **Hosting:** Currently Oracle Cloud (US region). A Mac Mini on-premises deployment for UGA is on the roadmap if procurement prefers in-house hosting.

I expect there will be a parallel privacy-office review and would welcome guidance on how to coordinate that with this SP registration.

### Timeline

The DOJ compliance date is now April 26, 2027 (per the April 20, 2026 IFR), which gives UGA a full year of runway — but a controlled rollout that starts with a small pilot in `[VERIFY: target term — Summer 2026? Fall 2026?]` is far preferable to a rush in early 2027. I'm hoping to begin SP integration within the next 2–4 weeks so a Mary Frances Early College of Education pilot can launch on that timeline. Happy to adjust to your standard SP onboarding cadence.

### Next step

What's the best way to proceed? I'm happy to:
- Send draft SP metadata for review
- Schedule a brief call to walk through the architecture
- Provide additional documentation to the privacy office in parallel
- Demo the live tool

Thanks for your help — I know this kind of request is well-trodden ground for EITS, and I'd rather follow your standard process than reinvent one.

Best,
Jennifer Kleiman
Doctoral student
Mary Frances Early College of Education, University of Georgia
jennifer.kleiman@uga.edu

---

## Notes for sender (not part of email)

- The "preprint forthcoming" + Kumar collaboration line is the credibility anchor. If the preprint posts before you send, switch to a direct arXiv link.
- The SP metadata commitment is honest about the implementation gap — better than promising metadata you don't have. EITS has seen this many times.
- The data-handling section pre-empts the privacy-office question rather than leaving it for them to surface. Lighter touch up front saves a round-trip.
- Do *not* use vendor-style language. This is faculty-to-IT inside the institution; collegial tone wins.
- The R4/Y4 retention policy reference should resolve to a real artifact before this email goes out. If the policy isn't drafted yet, soften that bullet to "in active drafting" rather than promising attachment.
- **Confirm with Dean Spangler before sending.** The letter names her as the originating contact and will be CC'd to her — she should explicitly bless that framing first. Even a one-line "yes, please send" via email is enough.
- Following the EITS KB process (article 160724) means the request should be routed through the standard channel (`idm@uga.edu`), not via individual EITS staff. Even if Dean Spangler offers a personal contact at EITS, the official email address gets you ticketed and tracked.
