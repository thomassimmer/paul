# Paul

**Apply more, apply better.**

[![CI](https://github.com/thomassimmer/paul/actions/workflows/ci.yml/badge.svg)](https://github.com/thomassimmer/paul/actions/workflows/ci.yml)

Paul is a self-hosted, open-source assistant for job hunting. Paste an offer: it
ranks it against your profile, writes a CV and a cover letter **in your own
template**, drafts the answers to the application form, and keeps every application
tracked on a single board.

It runs on your machine. Your profile, offers and documents never leave it — except
the text you send to the LLM provider you configure.

## Why

Applying seriously means repeating the same loop dozens of times: read the offer,
decide if it is worth it, adapt the CV, write a cover letter, answer the form,
remember what was sent, follow up. Most of it is mechanical, and every step is a
chance to give up.

Paul takes the mechanical parts and leaves you the decisions. Everything is
generated from a profile you own, every output is reviewed before it is used, and
nothing is ever sent for you.

## Features

| Module                | What it does                                                                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Profiler**          | Imports your CV, then interviews you one question at a time: the model reads the profile, asks what is missing, and writes each answer into it.  |
| **Offer analyzer**    | Paste the **raw HTML fragment** of an offer, or plain text. The agent extracts the structured offer and the application form's questions.        |
| **Filter and ranker** | Eliminates the offers that break your rules, scores the others against your profile and your wishes, with a one-sentence justification per axis. |
| **Writer**            | Writes the tailored CV, cover letter or both, **in your imported template**, drafts the form answers (including a form you paste when you decide to apply) and reports keyword coverage (ATS). |
| **Board**             | The home page: one table for every offer — score, verdict, status, follow-up.                                                         |
| **Tracker**           | Status, dates and notes for each application, with follow-up reminders.                                                                          |
| **Settings**          | LLM provider, model and key, follow-up delay, output language, CV and letter templates.                                                          |

## How it works

```mermaid
flowchart LR
    CV[CV import] --> P[Profiler]
    P -->|profile.yaml| DB[(SQLite + files)]
    H[Offer HTML / text] --> A[Offer analyzer]
    A -->|offer + form questions| R[Filter and ranker]
    DB --> R
    R -->|shortlist| W[Writer]
    DB --> W
    T[CV and letter templates] --> W
    W -->|application folder| F[/data/applications/.../]
    W --> B[Board]
    B -->|status, follow-ups| U((You))
```

Typical session:

1. **Once.** Import your CV, answer the interview — one question at a time, and you
   can stop and come back whenever you like — then import your templates, write your
   rules and your wishes.
2. **Per offer.** Paste the HTML fragment. The offer is analyzed, filtered and
   scored in seconds.
3. **For the best matches.** Open the offer, click _Prepare documents_, tick what
   applying actually asks for and paste the application form if the offer's page
   did not carry it. Review the generated CV, letter and answers, fix what you
   want, export, then apply on the company's site. Reopening the dialog later —
   say new questions came up — only writes what is missing: the CV and the letter
   that are already there are kept.
4. **Afterwards.** Update the status in the table and get reminded when a follow-up
   is due.

## Quick start

Requirements: Docker with Compose.

```bash
git clone https://github.com/thomassimmer/paul.git
cd paul
docker compose up
```

Then open <http://localhost:8000>, go to **Settings**, choose a model and paste your
API key. The first-run wizard walks you through importing your CV.

The app is bound to `127.0.0.1` only, and your data lives in `./data`, mounted as a
volume, so it survives updates (`git pull && docker compose up --build`).

### Without Docker (development)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The gates CI runs, on demand:

```bash
pytest                # the suite
pytest --cov          # the same, with coverage (config in pyproject.toml)
ruff check            # lint (config in pyproject.toml)
pyright               # types, read from the .venv named in pyproject.toml
```

## Principles

- **Local first.** Runs on your machine. Your profile, offers and documents never
  leave it, except the text sent to the LLM provider you configure.
- **Bring your own key.** Any provider (OpenAI, Anthropic, Mistral, Ollama, ...)
  through a single key or a local endpoint.
- **Your template, not a generic one.** Import your own CV and cover letter
  `.docx`: Paul reads their structure, fonts, colors and page setup, and renders the
  new documents into them. Without a template, a clean default is used.
- **No invented facts.** Generated documents may only use what is in your profile,
  and a verification pass flags anything unsupported. Factual form fields (name,
  salary expectation, notice period, ...) are read from your profile, never
  generated.
- **Explainable scores.** The model fills a fixed grid per axis and quotes its
  evidence; the total is computed in code. You can disagree with a specific line.
- **Human in the loop.** Review and edit before every export. No auto-apply, no
  scraping behind a login.
- **Plain files.** Profile in YAML, applications in folders of Markdown/DOCX. You
  can read, back up and version everything without the app.
- **Small and readable.** One process, one database file, no front-end build step.

## How this was built

Paul was written in a small number of concentrated sessions, mostly by directing an
AI coding agent (DeepSeek V4 Flash). It is worth saying plainly, because it changes how the code should
be read.

- **Directed, not generated on its own.** The architecture, the module boundaries,
  the data model and the security stance (untrusted offer text, no auto-apply,
  deterministic scoring) are deliberate choices, and each one is worth challenging in
  an issue.
- **Tests came with the code.** The suite is about as large as the application. That
  is what makes an AI-assisted codebase reviewable: it pins the behaviour that
  matters, and it is the first thing to run.
- **Human reviewed.** Every generated document passes through an editing loop, and
  the same goes for the code: the diff is read, the failing case is reproduced, and
  the behaviour is checked against the principles above before it lands.

The point is not the tooling. It is that "an agent helped write it" and "it is
trustworthy" are not in tension, as long as someone can explain every decision in
it — which is the bar this repository holds itself to.

## Data and privacy

- All data is stored under `./data` (git-ignored): profile, database, templates,
  application folders, settings and the API key. Don't commit or share this folder.
- What is sent to the LLM provider: profile excerpts, offer content and your rules.
  Use a local model (Ollama) if that is a concern.
- The web UI has no authentication because it is bound to `127.0.0.1`. Do not expose
  the port to a network.
- Pasted HTML is parsed as data and never executed, and prompts treat it as
  untrusted text.

## Configuration

| Setting           | Default                | Description                                                                                |
| ----------------- | ---------------------- | ------------------------------------------------------------------------------------------ |
| `model`           | none                   | LiteLLM model string, e.g. `anthropic/claude-sonnet-4-5`, `openai/gpt-4o`, `ollama/llama3` |
| `api_key`         | none                   | Provider key (not needed for local models)                                                 |
| `api_base`        | none                   | Custom or local endpoint                                                                   |
| `output_language` | `auto`                 | `auto` follows the offer; or `en`, `fr`, ...                                               |
| `followup_days`   | `7`                    | Days without news before a follow-up is suggested                                          |
| `target_pages`    | `2` (CV), `1` (letter) | Used by the fit check                                                                      |
| `show_get_started` | `true`                | Show the "Get started" checklist on the board; also hidden once every step is done         |

### Prompts

Every prompt is a Markdown file under `app/prompts/`, and every one can be
edited: from the settings page, or by dropping a file of the same name (for
example `ranking/score.md`) under `data/prompts/`. Delete that file — or use
“Use the default” — to go back to the built-in one. Some prompts name roles and
fields the code checks the answer against, so a careless edit can silently drop
generated content; the settings page warns about this. Editing a
ranking prompt marks the stored scores out of date, exactly like changing a rule
or a model does.

**Stack:** Python 3.12, FastAPI, Jinja2 + HTMX, SQLite, Pydantic, LiteLLM,
BeautifulSoup, `python-docx`, `pypdf`, Docker Compose.

## Roadmap

The MVP is complete: profiler, offer analyzer, filter and ranker, template-based
writer, tracker and board all work end to end.

Next:

- [ ] IMAP mailbox reading and status suggestions
- [ ] Fetch an offer from a URL (best effort; many sites need JavaScript or a login)
- [ ] PDF template look extraction improvements
- [ ] Interview preparation sheet per application
- [ ] Import/export of the whole workspace
- [ ] Multiple profiles (e.g. one per target role)

## Non-goals

- **No automatic submission** of applications, and no bots filling forms on your
  behalf: this violates most sites' terms and produces poor applications.
- **No scraping behind a login** (LinkedIn and similar). You paste what you see.
- **No hosted version or accounts.** Local tool only.
- **No guarantee on ATS behavior.** The score is a keyword indicator.

## The name

The name is a French pun. _Paul_ sounds like _Pôle_, so **Paul Emploi** is a
near-homophone of **Pôle Emploi**, the French public employment agency (now _France
Travail_).

## Contributing

Issues and pull requests are welcome. Please keep the code small and readable, add a
test for new behavior, leave `pytest`, `ruff check` and `pyright` green, and never
include real personal data in examples or bug reports (use the fictional profile in
`app/examples/`).

### A demo workspace

A throwaway workspace for trying things out, taking screenshots or recording a
demo. Both scripts refuse to touch a directory they did not create:

```bash
python scripts/seed_demo.py --data-dir ./demo-data --model deepseek/deepseek-flash
python scripts/anonymize_template.py --data-dir ./demo-data --report
```

`seed_demo.py` fills the directory with the fictional profile, five made-up offers,
their verdicts and one prepared application folder. It goes through the app's own
stores, so the scores, the rendered DOCX and the keyword coverage come from the real
code; only the two model steps — analyzing an offer, writing the documents — are
fixtures. Pass the model you will actually use: a stored score carries a fingerprint
of its inputs, the model included, and typing a different one later would flag every
offer as out of date.

`anonymize_template.py` rewrites an imported `.docx` template with the fictional
text while keeping its layout, so a demo can show a real template without showing a
real CV. `--report` prints what would change and writes nothing.

Then run the app on it, on another port so your own workspace can stay open. The
image is the one built in [Quick start](#quick-start):

```bash
docker run --rm -p 127.0.0.1:8001:8000 -v "$PWD/demo-data:/app/data" -e PAUL_DATA_DIR=/app/data paul

# or from the source tree, with LibreOffice installed for the PDF preview
PAUL_DATA_DIR=./demo-data uvicorn app.main:app --port 8001
```

It serves on <http://localhost:8001>. The profile there is Camille Moreau: if you
see your own name, or your own offers on the board, you are looking at your real
workspace rather than at the demo.

## License

MIT. See [LICENSE](LICENSE).

The one third-party file that ships with the app, the vendored htmx build, keeps
its own licence: Zero-Clause BSD, reproduced in
[`app/web/static/THIRD_PARTY.md`](app/web/static/THIRD_PARTY.md).
