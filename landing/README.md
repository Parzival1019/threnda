# Threnda landing page

Static page (`index.html`, `styles.css`, `form.js`, `config.js`, `img/`), no
build step and no external requests. Hungarian copy for the first pilots.
Screenshots in `img/` are real product screens taken from the demo instance
(fictional data only — see [docs/demo.md](../docs/demo.md)); retake them
with the same demo data if the UI changes.

## Before publishing — required configuration (currently MISSING)

The demo-request form does **not deliver anywhere** until one of these is set
in `config.js`:

1. `formEndpoint` (recommended): an HTTPS endpoint that accepts a `POST` of
   form fields (`name`, `email`, `company`, `team_size`, `message`,
   `consent`) and answers 2xx — e.g. a Formspree form URL or your own
   endpoint. Requires an account/decision: see
   [docs/pilot/uzleti-dontesek.md](../docs/pilot/uzleti-dontesek.md) #9.
2. `contactEmail` (fallback): opens the visitor's e-mail client pre-filled.

With both empty the form shows "not available" instead of pretending to
send. Also decide: domain and hosting (#10), a privacy notice link for the
consent checkbox (the form collects personal data — the notice text is a
legal decision and is not included).

## How delivery was verified

`landing/check_form.py` (run manually: `python landing/check_form.py`, needs `pip install playwright` and Google Chrome) serves the page,
points `formEndpoint` at a local test receiver, submits the form in headless
Chrome and checks that the receiver got every field and the page showed the
success message; it also checks the error path (receiver answers 500) and
the unconfigured state. Re-run it against the real endpoint after
configuring it, then send one real test request and confirm it arrives.
