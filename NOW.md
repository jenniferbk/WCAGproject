# NOW - Current Session State

## Project Status
- **Live site**: https://remediate.jenkleiman.com/
- **Server**: Oracle Cloud ARM instance at 150.136.101.132
- **Phase**: Post-launch — adding billing and polish

## Current Work: Stripe Billing Integration
- **Status**: Code complete, tests passing (28 new, 157 web tests green)
- **Files created**: `src/web/billing.py`, `tests/test_billing.py`
- **Files modified**: `pyproject.toml`, `src/web/app.py`, `src/web/static/index.html`, `.env`
- **Stripe CLI**: Installed locally (`brew install stripe/stripe-cli/stripe`)
- **Stripe account**: New account created (existing Discord one is platform-locked)
- **Stripe secret key**: Obtained, needs to be added to `.env`
- **Webhook**: Not yet created in Stripe dashboard — needs endpoint `https://remediate.jenkleiman.com/api/billing/webhook` listening for `checkout.session.completed`
- **Local dev webhook**: Use `stripe listen --forward-to localhost:8000/api/billing/webhook`

## Remaining Steps for Billing
- [ ] Add Stripe keys to `.env` locally
- [ ] Run `stripe login` and `stripe listen` for local testing
- [ ] Test full buy flow locally with test card 4242 4242 4242 4242
- [ ] Set up production webhook in Stripe dashboard
- [ ] Deploy to server (install stripe, add env vars, restart)
- [ ] Test production flow in Stripe test mode
- [ ] Switch to Stripe live mode when ready

## Up Next (After Billing)
- Production hardening
- End-to-end testing with real faculty documents
- Admin tooling improvements
