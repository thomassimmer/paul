# Paul (Emploi)

> Self-hosted, open-source assistant that helps you **apply to more jobs, and apply better**.
> Paste an offer, get a ranked shortlist, a tailored CV and cover letter in *your own template*, answers to the application form, and a tracker to follow it all.

---

## Table of contents

1. [Why](#why)
2. [Principles](#principles)
3. [Features](#features)
4. [Quick start](#quick-start)
5. [How it works](#how-it-works)
6. [Modules in detail](#modules-in-detail)
7. [Template import](#template-import)
8. [Data and privacy](#data-and-privacy)
9. [Tech stack and project layout](#tech-stack-and-project-layout)
10. [Configuration](#configuration)
11. [Roadmap](#roadmap)
12. [Non-goals](#non-goals)
13. [Contributing](#contributing)

---

## Why

Applying seriously means repeating the same loop dozens of times: read the offer, decide if it is worth it, adapt the CV, write a cover letter, answer the form questions, remember what was sent, follow up. Most of it is mechanical, and each step is a chance to make mistakes or give up.

Paul automates the mechanical parts with an LLM **while keeping you in control**: everything is generated from a profile you own and can edit, every output is reviewed by you, and nothing is ever sent automatically.

## Principles

- **Local first.** Runs on your machine. Your profile, offers and documents never leave it, except the text sent to the LLM provider you configure.
- **Bring your own key.** Any LLM provider (OpenAI, Anthropic, Mistral, Ollama, ...) through a single API key or a local endpoint.
- **No invented facts.** Generated documents may only use what is in your profile. A verification pass flags anything unsupported.
- **Human in the loop.** Review and edit before every export. No auto-apply, no scraping behind a login.
- **Plain files.** Profile in YAML, applications in folders of Markdown/DOCX. You can read, back up and version everything without the app.
- **Small and readable.** One process, one database file, no front-end build step.

## Features

| Module | What it does |
|---|---|
| **Profiler** | Imports your CV, interviews you to fill the gaps, and builds a detailed, editable profile. |
| **Offer analyzer** | Paste the **raw HTML fragment** of an offer (description + application form), or plain text. The agent extracts structured data and the form questions. |
| **Filter and ranker** | Eliminates offers that break your rules and scores the others against your profile and your wishes, with a one-sentence justification. |
| **Writer** | Produces a tailored CV and cover letter **in your imported template**, drafts answers to the form questions, and computes a keyword-coverage (ATS) score. |
| **Board** | The home page: one table for every offer — score, verdict, status, documents, follow-up. Ranking runs from a modal over it, and each offer opens as a single page. |
| **Tracker** | The status, the dates and the notes of each application, with follow-up reminders. Mailbox reading comes after the MVP. |
| **Settings** | LLM provider, model and API key, follow-up delay, output language, and the CV and letter templates. |

## Quick start

Requirements: Docker with Compose.

```bash
git clone https://github.com/thomassimmer/paul-emploi.git
cd paul-emploi
docker compose up
```

Then open <http://localhost:8000>, go to **Settings**, choose a model and paste your API key. The first-run wizard walks you through importing your CV.

The app is bound to `127.0.0.1` only. Your data lives in `./data`, which is mounted as a volume, so it survives updates (`git pull && docker compose up --build`).

Rebuilds are cheap: the Dockerfile installs the dependencies from `pyproject.toml` before copying the source, with placeholders standing in for the package and its README, and keeps the pip and apt caches in BuildKit mounts. Editing a file under `app/` — or in this README, which is only metadata — therefore rebuilds in about a second instead of reinstalling every wheel. Only a change to `pyproject.toml`, or to the apt package line (which re-runs the LibreOffice install once), invalidates the install layer.

### Without Docker (development)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest
```

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

1. **Once:** import your CV, answer the profiler's questions, import your CV and letter templates in Settings, write your filter rules and wishes.
2. **Per offer:** paste the HTML fragment. The offer is analyzed, filtered and scored in seconds.
3. **For the best matches:** click *Prepare* in the table, or open the offer and prepare it there. Review the generated CV, letter and answers, fix what you want, regenerate a section if needed, export, apply on the company site.
4. **Afterwards:** update the status in the table; get reminded when a follow-up is due.

## Modules in detail

### 1. Profiler

**Input:** an existing CV (PDF or DOCX), or its text pasted in.

1. **Extraction (deterministic).** Text is read locally (`pypdf`, `python-docx`), and the raw text is kept in `data/profile/cv.txt` so the draft can be re-run later with a better model.
2. **First draft (LLM, structured output).** The model fills a `ProfileDraft`, which deliberately has **no** `facts` and **no** `preferences` fields: those are yours to answer, and the type makes it impossible for the model to invent them. Facts and preferences are also preserved when you re-import a CV.
3. **Interview.** Paul asks **one question at a time**, targeting what a CV usually omits: what the company was, your exact remit, team size, the stack you really used, measurable results, the hard problems, then the facts and what you want more or less of. The questions are computed by code from your profile, not by the LLM, so the same profile always yields the same questions; a skipped question is remembered in SQLite and can be brought back.
4. **Answer structuring (LLM, optional).** When an answer describes results and a model is configured, the model splits it into achievements with their `metrics` and `skills`. It is never trusted blindly: each item must quote the excerpt of your answer it came from, and it may not state a number your answer does not contain. A breakdown that fails either check is discarded whole and your own lines are kept as written, one achievement per line. Nothing is ever added to what you wrote.
5. **Editable profile.** Correct anything in the structured editor, or directly as YAML. You can also edit the file by hand at any time.

**Storage:** `data/profile/profile.yaml`, a single readable file, next to the extracted `cv.txt`.

```yaml
identity:
  name: ...
  first_name: ...
  last_name: ...
  location: ...
  email: ...
  phone: ...
  links: [...]
facts:                     # answered by you, never by the LLM
  work_authorization: ...
  work_permit_expiry: ...  # only if you hold a permit or visa
  notice_period: ...
  salary_expectation: ...
  languages: [...]
experiences:
  - id: exp-acme-2022
    company: Acme
    title: Lead Software Engineer
    period: 2022-03 / 2024-06
    context: ...           # what the company and the role were
    team_size: 7 engineers
    stack: [Rust, Kafka]
    difficulties: ...
    achievements:
      - id: exp-acme-2022-a1
        text: Cut ingestion latency by 60% by ...
        metrics: ["60%"]
        skills: [Rust, Kafka]
education: [...]
projects: [...]
skills: {Languages: [Rust], Tools: [Kafka]}
preferences:
  more_of: [...]
  less_of: [...]
```

Every experience and achievement has a stable `id` (`exp-acme-2022`, `exp-acme-2022-a1`), derived from its content so that a re-import does not churn the ids. The writer cites these ids, which is what makes the fact-checking pass possible. A complete fictional example lives in `app/examples/profile.example.yaml`.

### 2. Offer analyzer

**Input (any combination):**

- the **HTML fragment** of the offer page: description *and* application form (recommended, this is the main path),
- plain text pasted from the page,
- several fragments for the same offer (e.g. description and form on different pages). Duplicated lines across fragments are collapsed.

**How to get the fragment:** open the offer, right-click the relevant block, *Inspect*, right-click the element, *Copy outerHTML*, paste it into Paul. Content loaded in another frame, or behind another click, has to be pasted separately.

**Processing:**

1. **Cleaning (deterministic).** `BeautifulSoup` drops scripts, styles, SVG, images, hidden elements and tracking parameters, and keeps what matters: headings, lists, paragraphs and links (rendered as Markdown). Form controls are parsed here too, into `label`, `name`, `type`, `required`, `maxlength`, `placeholder` and `<option>` values, with radio and checkbox groups merged by `name` and named by their `<legend>`. This keeps token usage low and makes extraction reliable.
2. **Extraction (LLM, structured output), in the background.** The cleaned **description** is turned into a validated Pydantic object. A model call takes seconds, so the page does not wait on it: cleaning and the input checks answer at once, the extraction runs as a background run, and the page shows a small card that polls and then moves you to the new offer.

```text
Offer
├── title, company, location, remote policy, contract type, seniority
├── salary (if present), language of the offer
├── responsibilities
├── requirements: must-have / nice-to-have
├── keywords (with the variants the text itself uses)
├── constraints: work authorization, citizenship, on-site, language level, clearance
├── company info found in the text (size, funding, domain, mission)
└── application form          <- parsed from the markup by code, not by the LLM
    └── questions: label, type, options, required, max length
```

The application form is deliberately *not* asked for from the model: labels, `required`, `maxlength` and option values are facts sitting in the markup, so they are read by code (cheaper, and never approximated). Form controls are therefore left out of the text sent to the model.
3. **Storage.** The offer is saved in SQLite as JSON, next to the raw fragment and the cleaned text, so you can re-run the analysis later with a better model or a tighter prompt without pasting the page again. The **posting URL** is the one field filled in by hand, on the offer's *Complete / edit* page: the analyzer only ever sees the text you pasted, never the address bar. It is kept when the offer is analyzed again, and shown on the offer page as an *Open posting* link, so the posting can be found again when an application moves forward.

Nothing is invented: a field the offer does not state stays empty. Company facts are only recorded when the pasted text mentions them. If the extraction fails validation it is retried once with the error message, and whatever could not be read is listed on the offer page and can be completed by hand.

### 3. Filter and ranker

Two stages, both run against your own data. Your elimination rules and your
weighted wishes are edited in the **ranking modal** on the board and stored with
your settings; *Run ranking* applies them to every offer the chosen scope selects.

**Stage 1: elimination.** You write natural-language rules once, for example:

> Eliminate offers that explicitly require citizenship, a security clearance, or an existing work permit.
> Eliminate offers requiring fewer than 3 or more than 10 years of experience.

Per offer, the model returns `eliminated` plus the rule it applied and the exact
excerpt that triggered it. An elimination missing either one is discarded **in
code**: nothing disappears on an impression. Eliminated offers stay visible with
their reason and can be restored in one click.

**Stage 2: scoring.** For the offers that survive, the model fills a fixed grid:

| Axis | Source | Weight |
|---|---|---|
| Technical match | requirements vs. profile skills and achievements | 0.40 |
| Seniority and scope | responsibilities vs. experience | 0.20 |
| Your wishes | your weighted criteria, e.g. *startup, funded, scientific domain, remote* | 0.25 |
| Red flags | vague role, unrealistic requirements, contradictions | 0.15 |

Each axis comes back with a score from 0 to 5 and a one-sentence justification.
The weights are fixed in code and the total (0-100) is computed by code, never by
the model: that is what makes a score explainable (you can disagree with a
specific line) and stable (the same grid always gives the same total). Company
facts (funding, domain) are only known if they appear in the pasted content: the
ranker says "unknown" rather than guessing.

A manual decision always wins over the rules. Restoring an offer keeps it
restored across later runs; eliminating one by hand drops its score, so a
restored offer is scored again the next time you run the ranking.

**Running it.** A run is a background task, not a request that blocks for two
minutes: the page shows its progress, the table refreshes as offers come in, and
*Stop* ends it (the calls already in flight finish, no new one starts). What gets
ranked is your choice:

| Scope | What it ranks |
|---|---|
| Not ranked yet, or out of date *(default)* | only what needs it |
| Every offer | recomputes everything |
| Only the offers I have not applied to yet | uses the tracker's statuses |
| Only the offers I tick | an explicit selection |

"Out of date" is decided without asking the model anything: each ranking stores a
fingerprint of what produced it (rules, wishes, profile, offer, model). Change one
of those and the offers affected are flagged *out of date* and picked up again —
and nothing else is. Adding three offers to a batch of twenty therefore costs six
calls instead of forty, and changing your wishes only re-ranks what that change
affects. The number of calls in flight is configurable (1 to 8, default 4); lower
it if your provider complains about the rate.

### 4. Writer

For an offer you pick, Paul builds an **application folder** containing:

```text
data/applications/2026-09-acme-senior-backend/
├── offer.json          # structured offer, as stored, with the id it was analyzed under
├── offer.html          # raw fragment as pasted
├── cv.md               # editable source of the CV
├── cv.docx             # rendered in your template
├── letter.md
├── letter.docx
├── answers.md          # form questions and drafted answers
├── ats.json            # keyword coverage report
└── notes.md            # your notes, interview prep
```

The board's **Documents** column says which offers already have a folder and is
where *Prepare* is clicked; the offer page carries the same button, so you never
have to guess where the writing starts. Preparing is a background task with a
step-by-step report — the page refreshes itself every 2 seconds and can stop it —
because it is three or four model calls plus a page measurement, and a slow
provider should not leave you staring at a spinner. It needs an imported profile
and a configured model. Writing into an existing folder updates it in place rather
than creating a second one, and the folder is remembered on the application.

**Tailoring.** The writer selects and orders the most relevant experiences and achievements from your profile, rephrases them with the offer's vocabulary, and writes a letter grounded in the company and the role. Documents are generated in the **language of the offer** (or the one you force in settings).

**No invented facts.**

- The prompt only allows content from the profile and requires each statement to cite an achievement `id`.
- A second pass (*grounding check*) compares every line of the output to the profile and flags unsupported claims, which are highlighted in the review screen.
- Factual form questions (your name, first name, last name, email and phone, then work authorization, salary expectation, notice period, relocation, languages, ...) are answered **from your profile** (``identity`` and ``facts``), never generated. If a fact is missing, the field is left empty for you.

**Form answers.** Open questions ("Why do you want to join us?") get a draft that respects the max length, using your profile and the offer.

**Review screen.** Side-by-side preview and editor for CV, letter and answers,
shown as sections of the offer page; a sticky section nav, and a *Checks* card that
folds away so the long page stays short. Buttons above each editor add a line under
the role you pick, so the `[role]` prefix never has to be typed. Regenerate a
section with an instruction ("shorter", "more focus on data engineering").
Regenerating runs in the background too, with the same progress panel, and the
sections refresh themselves when it lands. Then export.

**ATS score.** There is no universal ATS score: ATS (*Applicant Tracking System*, e.g. Workday, Greenhouse, Lever) is the software companies use to receive and sort applications, and vendors' "scores" are in practice keyword coverage. Paul does the same, transparently:

1. The LLM extracts the important keywords from the offer, with accepted variants (`K8s` / `Kubernetes`) — or reuses the ones the offer analysis already found, which is the usual case.
2. Plain code checks their presence in the final CV text (normalized, accent- and case-insensitive).
3. You get a **coverage percentage and the list of missing keywords**, each with a hint on whether your profile supports adding it truthfully.
4. **Format checks** on the DOCX: text boxes, images holding text, multi-column layouts and tables used for layout are flagged, since some parsers handle them poorly.

Treat it as an indicator, not a guarantee.

### 5. The board

The home page is one table: **one row per analyzed offer**, and everything the app
knows about an offer is on it.

| Column | Content |
|---|---|
| (tick) | selects the offer for the ranking run's *only the offers I tick* scope |
| Company / role | link to the offer page |
| Score | the ranking total, when there is one, with *not ranked* and *out of date* tags |
| Verdict | *Kept* or *Eliminated*, marked when you overrode the rules by hand |
| Status | `Analyzed` → `Shortlisted` → `Ready` → `Applied` → `Interview` → `Offer` / `Rejected` / `No response` |
| Documents | *Prepare*, then *Review* once the folder exists |
| Follow-up | highlighted when *N days* (configurable, default 7) have passed with no news |

A filter switches between *Follow-up due*, *Not applied to yet*, every status, or
everything, and any column sorts. The status is changed from the row itself, and
moving to an applied status records **today's date** so the follow-up clock starts
on its own. Nothing is inferred: the follow-up counts from the last contact, or
from the application date when there has been none, and only a status still
*Applied* waits for news.

**Ranking** lives in a modal over the table — the elimination rules, the weighted
wishes, the pace, and the run — because the run's selection is the table's own
ticks. Progress is reported in the modal, and the table refreshes itself as the
scores arrive.

Opening an offer gives **one page** for everything about it: the structured offer
and its actions, its verdict with the scoring axes and the elimination evidence,
the tailored CV, cover letter and form answers, and the tracking form. Once you
have saved the posting URL by hand, the same page links straight to the original
listing.

**Quick navigation.** The four long pages — the board, the offer page, the profile
and the settings — carry a list of their sections. On a wide screen it is a sticky
column on the left, in the room those pages leave empty, and the section you are
reading is marked as you scroll; below that width the same list is the horizontal
bar it started as, so a narrow window loses nothing. The offer page groups what
the offer says under one heading and the application under another, and grows the
document sections when a preparation adds them.

The onboarding checklist sits above the table and folds itself away once every
step is done; it can always be reopened.

Statuses are changed by hand in the MVP. **After the MVP:** one click reads your
mailbox over **IMAP with an app password** (no OAuth setup), matches messages to
applications, proposes status updates (acknowledgment, interview, rejection) and
asks you to confirm.

The *Not applied to yet* filter is also what the ranker's **“only the offers I
have not applied to yet”** scope uses, so you can rank what you have not sent
yet without re-ranking the rest.

### 6. Settings

One page, opened from the navigation:

- LLM provider, model, API key or local endpoint (through LiteLLM), with a "test connection" button
- Output language (auto / fixed)
- Follow-up delay
- Filter rules and weighted wishes (edited in the board's ranking modal, stored with the settings)
- **Templates**: import your CV and cover letter `.docx`, check the roles that were
  detected, or go back to the default. This is a section of the page rather than a
  page of its own; it sits outside the settings form because its upload forms
  cannot be nested inside it.

## Template import

Your CV and letter should look like *you*, not like a generic export. Paul learns from **your own templates**.

### What you provide

A CV and a cover letter as `.docx` files, ideally a previous version you are happy with. They are imported separately and stored in `data/templates/`.

### What is captured

Structure, colors, fonts, page setup and decorative elements, all read directly from the file:

- page size and margins, headers and footers, column layout (including two-column layouts built with tables)
- fonts, sizes, colors, spacing, bullet styles, borders and shading, theme colors
- the **order of sections** and how each kind of content is styled

### How it is reused

1. **Analysis.** A parser walks the document — table cells included — and groups its content into blocks. Each block gets a **role**:

   `name`, `headline`, `contact`, `section_title`, `entry_title`, `entry_subtitle`, `entry_dates`, `bullet`, `body_text`, `skill_line`, `recipient`, `date`, `salutation`, `closing`, `signature`, or `fixed`.

   The role comes first from the layout, which already says a lot (a bullet style is a bullet, a short bold line is a title, a line of dates is not a phone number), then one LLM call settles the rest. Without a model, the layout alone decides.
2. **Blueprint.** The result is stored as JSON next to your file, and shown as a preview where every block's role can be corrected in a select. A wrongly read role would silently produce a wrongly styled document, so it is meant to be checked.
3. **Rendering.** The original DOCX is used as the base, and its body is rebuilt: for each line, the XML of the block it was modelled on (its **prototype**) is deep-copied and its text replaced. Fonts, sizes, colours, spacing, numbering, borders and shading therefore come from your file rather than being approximated, and page size, margins, styles, theme, headers and footers are untouched because the file itself stays the base. A role your template does not show (e.g. *Projects* when there is no such section) falls back to the closest role it does show.
4. **Fit check.** The document is converted to PDF to count pages. If it overflows the target length, the writer condenses the content and re-renders, up to two times. Without LibreOffice (`WITH_PDF=0`), a line-count estimate is used instead and says so.

If you provide no template, a clean, single-column, parser-friendly default is used — built in code, so it needs no analysis and no model call.

### Limitations

- Templates built from text boxes, floating shapes or images of text may be only partially understood; the preview tells you what was detected.
- A **PDF** template (e.g. exported from Canva) is not supported in this version. Converting it to DOCX first is the only way in.
- A **two-column layout built with a table** is analyzed (you see its blocks and can set their roles), but rendering re-emits those paragraphs at body level: the result is single-column. Reproducing an arbitrary table layout cell by cell is a bigger job than it looks.
- A decorative element *inside the body* (`fixed`) is not carried over either, since the body is rebuilt; anything in the **header or footer** is preserved, which is where logos and page numbers usually live.
- Exotic Word features may not survive; report them with an anonymized sample file.

### Exports

- **DOCX** (always) and **Markdown** (always).
- **PDF** through headless LibreOffice, included in the Docker image (larger image, about 500 MB). Build without it with `docker compose build --build-arg WITH_PDF=0`; the fit check then falls back to an estimate.
- **Preview.** The document sections of the offer page frame the rendered PDF, so you see the real layout and *where the page break falls*. It is converted on demand from the DOCX you last saved, so it refreshes when you save. Without LibreOffice, it falls back to a plain HTML preview that cannot show the pagination.

## Data and privacy

- All data is stored under `./data` (git-ignored): profile, database, templates, application folders, settings.
- The API key is stored in `data/settings.json` on your machine. Do not commit or share this folder.
- What is sent to the LLM provider: profile excerpts, offer content and your rules. Use a local model (Ollama) if that is a concern.
- The web UI has no authentication because it is bound to `127.0.0.1`. Do not expose the port to a network.
- Pasted HTML is parsed as data and never executed. Since the content comes from third-party pages, the prompts treat it as untrusted text and ignore instructions embedded in it.

## Tech stack and project layout

**Stack:** Python 3.12, FastAPI, Jinja2 + HTMX (no front-end build), SQLite, Pydantic, LiteLLM, BeautifulSoup, `python-docx`, `pypdf`, Docker Compose.

```text
paul-emploi/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── README.md
├── app/
│   ├── main.py                # composition root: app, lifespan, routers
│   ├── config.py              # settings load/save
│   ├── db.py                  # SQLite access
│   ├── llm.py                 # LiteLLM wrapper, structured output + retry
│   ├── background.py          # single-call background runs, polled by their page
│   ├── models.py              # Pydantic models: Profile, Offer, Score, Application
│   ├── prompt_context.py      # profile/offer/wishes/language as the prompts see them
│   ├── prompts/               # prompts as plain text files
│   │   ├── offers/
│   │   ├── profiler/
│   │   ├── ranking/
│   │   └── writer/
│   ├── profiler/              # CV import, interview, editable profile
│   │   ├── cv.py              # PDF/DOCX text extraction
│   │   ├── draft.py           # LLM: CV text -> ProfileDraft
│   │   ├── editor.py          # structured profile form <-> Profile
│   │   ├── ids.py             # stable ids (exp-acme-2022-a1)
│   │   ├── interview.py       # gap-driven questions, answer merge
│   │   ├── router.py          # HTTP routes
│   │   ├── service.py         # import orchestration
│   │   ├── store.py           # profile.yaml, cv.txt, interview state
│   │   └── text.py            # list, metric and text helpers
│   ├── offers/                # offer analyzer
│   │   ├── clean.py           # deterministic HTML cleaning + form parsing
│   │   ├── editor.py          # structured offer form <-> Offer
│   │   ├── extract.py         # LLM: cleaned text -> OfferDraft
│   │   ├── router.py          # the offer page, and the analyzer's actions
│   │   ├── service.py         # analysis orchestration
│   │   └── store.py           # offers in SQLite (raw + cleaned kept)
│   ├── ranking/               # filter and ranker
│   │   ├── eliminate.py       # stage 1, with the quote-it-or-drop-it guard
│   │   ├── jobs.py            # background run: progress, stop, bounded calls
│   │   ├── router.py          # HTTP routes
│   │   ├── score.py           # stage 2, weights and total computed in code
│   │   ├── service.py         # scope, staleness, and one offer at a time
│   │   └── store.py           # rankings in SQLite (verdict, override, score)
│   ├── writer/                # tailoring, grounding check, form answers
│   │   ├── answers.py         # factual questions, read from the profile (identity and facts)
│   │   ├── draft.py           # LLM steps, and the role repair around them
│   │   ├── grounding.py       # every claim checked against the profile, in code
│   │   ├── jobs.py            # background job: steps, progress, stopping
│   │   ├── markdown.py        # the editable `[role] text {ids}` form
│   │   ├── router.py          # prepare, save, regenerate, download, preview
│   │   ├── service.py         # prepare, save, regenerate: the whole orchestration
│   │   ├── store.py           # application folders on disk
│   │   └── view.py            # the context of the document sections
│   ├── ats.py                 # keyword coverage and format checks
│   ├── templates_engine/      # DOCX analysis, blueprint, rendering
│   │   ├── analyze.py         # LLM reads the block roles, guesses as a fallback
│   │   ├── extract.py         # blocks read straight from the DOCX
│   │   ├── render.py          # rebuild the base document line by line
│   │   ├── router.py          # import, correct the roles, reset
│   │   └── view.py            # what the settings page shows about them
│   ├── tracker/               # statuses, follow-ups, (later) IMAP
│   │   ├── router.py          # a status from a row, one application's tracking
│   │   ├── service.py         # statuses, dates, the follow-up rule
│   │   └── store.py           # applications in SQLite
│   ├── web/                   # presentation layer
│   │   ├── templating.py      # Jinja env, render/redirect/local_url helpers
│   │   ├── board.py           # the one table: its rows, filter and sort
│   │   ├── routes/            # board (the home page), settings
│   │   ├── templates/         # Jinja templates
│   │   └── static/            # CSS, vendored HTMX
│   └── examples/              # fictional profile, sample templates
├── tests/
└── data/                      # git-ignored, mounted as a volume
```

Design rules: prompts live in files, not in code; every LLM call returns a validated Pydantic object; no HTTP request waits on a model call (the slow ones run as background runs or jobs their page polls); anything that can be computed by code (totals, keyword matching, follow-up dates, interview questions) is not left to the LLM.

Conventions: each feature package owns its HTTP routes (`app/profiler/router.py`) and keeps them thin, delegating to its own modules; the shell routes live in `app/web/routes/` and `app/main.py` only wires routers together.

## Configuration

| Setting | Default | Description |
|---|---|---|
| `model` | none | LiteLLM model string, e.g. `anthropic/claude-sonnet-4-5`, `openai/gpt-4o`, `ollama/llama3` |
| `api_key` | none | Provider key (not needed for local models) |
| `api_base` | none | Custom or local endpoint |
| `output_language` | `auto` | `auto` follows the offer; or `en`, `fr`, ... |
| `followup_days` | `7` | Days without news before a follow-up is suggested |
| `target_pages` | `2` (CV), `1` (letter) | Used by the fit check |

## Roadmap

**MVP**

- [x] Docker Compose install, settings page
- [x] Profiler: CV import, interview, editable profile
- [x] Offer analyzer: HTML fragment and text, form question extraction
- [x] Filter and ranker with explainable scores
- [x] DOCX template import and rendering (CV and letter)
- [x] Writer with grounding check, form answers, ATS coverage
- [x] Tracker with manual statuses and follow-up reminders
- [x] Ranker scope "not applied yet", fed by the tracker's statuses

**Next**

- [ ] IMAP mailbox reading and status suggestions
- [ ] Fetch an offer from a URL (best effort; many sites need JavaScript or a login)
- [ ] PDF template look extraction improvements
- [ ] Interview preparation sheet per application
- [ ] Import/export of the whole workspace
- [ ] Multiple profiles (e.g. one per target role)

## Non-goals

- **No automatic submission** of applications, and no bots filling forms on your behalf: this violates most sites' terms and produces poor applications.
- **No scraping behind a login** (LinkedIn and similar). You paste what you see.
- **No hosted version or accounts.** Local tool only.
- **No guarantee on ATS behavior.** The score is a keyword indicator.

## Contributing

Issues and pull requests are welcome. Please keep the code small and readable, add a test for new behavior, and never include real personal data in examples or bug reports (use the fictional profile in `app/examples/`).

## License

MIT (suggested, adjust as you prefer).

### Third-party

- The Docker image installs **LibreOffice** (MPL-2.0) from Debian packages and calls it headless for PDF export, page counting and the preview. It is a separate program invoked as a subprocess, not a linked library, so this project's MIT license is unaffected. Distributing the image does redistribute LibreOffice: keep its license notices (`/usr/share/doc/libreoffice*/copyright`) and point to its sources.
- Fonts: the image bundles **DejaVu** (Bitstream Vera/Arev license). No proprietary font is shipped; a template that references **Calibri** (Microsoft) relies on LibreOffice's substitution, and adding the metric-compatible **Carlito** (SIL OFL 1.1) is what keeps the page breaks close to Word's.
