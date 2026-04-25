---
status: verification notes — reference only, not a deliverable
last_verified: 2026-04-25
---

# UGA EITS Identity Federation — Process Verification

Notes from a quick external verification pass before sending the EITS letter. Saved here so future sessions don't have to re-verify and so the EITS letter's claims are anchored to known sources.

## Confirmed

- **Contact email:** `idm@uga.edu` is the live channel for third-party application registration with UGA's Federated Identity Provider. Source: EITS KB article [160724](https://uga.teamdynamix.com/TDClient/3190/eitsclientportal/KB/ArticleDet?ID=160724) — direct quote: *"UGA faculty and staff can submit a request by emailing idm@uga.edu to register third-party sites and applications in UGA's Federated Identity Provider."*
- **Intake mechanism:** email-only. No specific intake form exists; the KB article describes the email approach without referencing a service-catalog entry or ticket form.
- **Process flow:** *"Once a request has been submitted, the IDM team will review it and contact the applicant."* The article gives no SLA / typical timeline.
- **UGASSO recommendation:** EITS recommends important applications also enroll in UGASSO *"to ensure ongoing support if the UGA experiences issues with the Federation provider."* The article does not explain what UGASSO is or how to enroll separately, so the EITS letter asks EITS to advise on this directly.
- **Protocol:** EITS implements SAML (per the KB article). No specific version, profile, or signing requirements are documented publicly.

## Not documented publicly (will come in the back-and-forth)

These are all questions to expect EITS to answer (or to ask) once the initial email lands:

- IdP entity ID and metadata URL (production endpoint)
- Supported SAML profiles (SAML 2.0 Web Browser SSO is overwhelmingly likely but not stated)
- Attribute release policy and approval workflow (`eduPersonPrincipalName`, `mail`, `displayName` requested as the minimum)
- NameID format preference (transient vs persistent vs email)
- Signing algorithm preferences and certificate requirements
- AuthnContext / MFA expectations
- Whether a test/staging IdP is available
- UGASSO enrollment process and how it interacts with federated SP registration
- Typical onboarding timeline / SLA

## What this means for the EITS letter

- The letter's `[VERIFY: EITS contact]` placeholder resolves to `idm@uga.edu`. Done.
- Asking EITS for the IdP metadata, attribute policy, NameID requirements, and UGASSO process is appropriate — those aren't published, so EITS expects to provide them.
- Don't promise SAML SP metadata in the initial email; commit to delivering it on a stated timeline once requirements are received. The current letter draft already does this.

## Sources

- KB article 160724: https://uga.teamdynamix.com/TDClient/3190/eitsclientportal/KB/ArticleDet?ID=160724
- EITS InfoSec landing: https://eits.uga.edu/access_and_security/infosec/
- Service catalog (if a ticket route is later required): https://uga.teamdynamix.com/TDClient/3190/eitsclientportal/Requests/ServiceCatalog
