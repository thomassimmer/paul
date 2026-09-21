# Security

## Reporting a vulnerability

Use GitHub's private reporting: **Security → Report a vulnerability** on this
repository. It keeps the report private until there is a fix.

If that form is not available to you, open a regular issue that says only that you
have something to report, and no details; a private channel will be arranged from
there. Please do not paste an API key, a real CV or a real offer into an issue —
use the fictional profile in `app/examples/`.

Expect a first answer within a few days. Paul is a small self-hosted tool
maintained in personal time, so there is no bounty and no formal SLA.

## What is in scope

Anything that breaks one of the promises the README makes. Concretely:

- **Your data leaving the machine** other than in the text sent to the LLM provider
  you configured. That includes the API key stored in `data/settings.json`.
- **A web page reaching a running Paul.** The UI has no authentication and is meant
  to be reached on loopback only; `app/web/security.py` is what refuses the requests
  a browser can be made to send on another site's behalf. A way around it is a bug.
- **Pasted content being acted on.** The offer HTML is parsed as data and is never
  executed, and prompts treat it as untrusted text. If pasted content can reach the
  filesystem outside an application folder, the network beyond the configured
  provider, or the shell, that is a bug.
- **Escaping an application folder.** `app/writer/store.py` and the download route
  build file names from offer text and from the request; a traversal out of
  `data/applications/` is a bug.

## What is not

These are deliberate, and a report about them will be closed with a pointer here:

- **No authentication.** The app is a local tool, published on `127.0.0.1` only. Do
  not expose the port to a network, and see `PAUL_ALLOWED_HOSTS` if you put a
  reverse proxy in front of it.
- **The API key is stored in plain text** in `data/settings.json`. That file is
  yours, the README says so, and the alternative would be a keychain this project
  does not want to own.
- **A prompt injection that produces a poor document.** The offer text is written by
  a third party and the model may be talked into something silly; every document is
  reviewed before it is used, and no sheet is submitted for you. This becomes a
  security issue only if it leaks data or escapes a folder, as above.
- **Anything requiring an attacker who already runs code on your machine** or can
  write to `data/`: they already have everything the app has.
