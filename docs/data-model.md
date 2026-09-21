# The data model

Paul keeps your workspace in two places, and the split is deliberate:

- **`data/`** holds what you can read, edit, back up and version without the app —
  the profile, the templates, the generated documents, the settings.
- **`data/paul.sqlite3`** holds what does not belong in a readable file: what the
  interview has already asked, the offers, our verdict on them, and what you did
  about them.

The schema itself lives in one place, [`app/db.py`](../app/db.py), commented table
by table. This page is the half no single module can show: how the two stores line
up, and which column carries which decision.

## The directory

```
data/                          # PAUL_DATA_DIR, /app/data in Docker
  settings.json                # Settings — model, criteria, and the API key in clear
  paul.sqlite3                 # the tables below
  profile/
    profile.yaml               # your Profile, a file you own and may edit by hand
    cv.txt                     # the CV text as imported, kept for a re-draft
  prompts/                     # your overrides; same relative names as app/prompts/
    ranking/score.md
  templates/
    cv.docx     cv.json        # your template, and the roles read out of it
    letter.docx letter.json
  applications/
    2026-03-acme-analytics-lead-backend-engineer/
      offer.json               # the OfferRecord this folder was made for
      offer.html               # the fragment exactly as pasted (text pastes too)
      cv.md        cv.docx     # the editable source, and the rendered document
      letter.md    letter.docx
      answers.md               # the form questions, answered, and where each came from
      ats.json                 # keyword coverage and the format red flags
      notes.md                 # created once, never overwritten
```

## The tables

### `offers` — what the offer says

| Column        | Meaning                                                                                                                          |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `id`          | The offer's identity, and the key everything else hangs off.                                                                     |
| `analyzed_at` | When it was last read.                                                                                                           |
| `source`      | `html` or `text` — what was pasted. Display only.                                                                                |
| `url`         | The link to the posting. Required at import, because the analyzer never sees an address bar.                                     |
| `raw`         | The pasted fragment, byte for byte.                                                                                              |
| `cleaned`     | The Markdown-ish text the model actually read. Kept so an offer can be re-analyzed with a better model without pasting it again. |
| `offer_json`  | The `Offer`, serialized: everything the model extracted, plus the form questions that code parsed.                               |

`raw` and `cleaned` are both kept because they answer different questions: `raw` is
what you pasted, `cleaned` is what the extraction ran on.

### `rankings` — what we think of the offer

One row per ranked offer, deliberately **not** a column on `offers`: this is our
judgement, and it has to be replaceable — by a re-run, or by you — without touching
what the offer says.

| Column            | Meaning                                                                                                                                                                                                                                |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `offer_id`        | Primary key and foreign key to `offers`, `ON DELETE CASCADE`. One verdict per offer.                                                                                                                                                   |
| `eliminated`      | Stage 1's answer, before any override.                                                                                                                                                                                                 |
| `rule`, `excerpt` | The candidate's rule that eliminated it, and the offer text that triggered it. An elimination missing either is downgraded to nothing, in `eliminate.build_elimination` — that is what keeps an offer from vanishing without a reason. |
| `override`        | `kept`, `eliminated`, or empty to trust the rules again. It wins in `models.effective_eliminated`, and it survives a re-run: `rank_one` reuses the stored elimination rather than paying for the model call again.                     |
| `score_json`      | The `Score`: four axes with their justifications and weights, and a total computed in code. `NULL` when eliminated.                                                                                                                    |
| `fingerprint`     | A short hash of everything that produced the verdict — the model, the rules, the wishes, the text of the ranking prompts, the profile, the offer.                                                                                      |
| `scored_at`       | When.                                                                                                                                                                                                                                  |

The fingerprint is the mechanism behind _out of date_: change a rule, a wish, the
profile, the offer, the model, or the wording of `ranking/score.md`, and the stored
score stops matching. `service.is_stale` compares the stored hash with a freshly
computed one, and the `pending` ranking scope means exactly "never ranked, or
stale". No model call is needed to notice it.

### `applications` — what you did about the offer

One row per application you have touched. **No row means the default status**,
`analyzed` — which is why analyzing an offer is enough to put it on the board.

| Column                   | Meaning                                                                                                                                   |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `offer_id`               | Primary key and foreign key to `offers`, `ON DELETE CASCADE`.                                                                             |
| `status`                 | One of `tracker.service.STATUS_LABELS`. Normalized on the way in: an unknown value falls back to `analyzed` rather than being stored.     |
| `applied_on`             | `YYYY-MM-DD`. Set the first time the status becomes one of `APPLIED_STATUSES`, so the follow-up clock starts without you typing anything. |
| `last_contact`           | `YYYY-MM-DD`, the date the follow-up counts from. A later contact resets it.                                                              |
| `notes`                  | Free text from the offer page.                                                                                                            |
| `folder`                 | The application folder the writer wrote into, once it has run.                                                                            |
| `want_cv`, `want_letter` | What applying to _this_ offer needs. Decided when preparing it, not when analyzing it.                                                    |
| `form_source`            | The form you pasted, when the offer's own fragment did not carry it. Empty means the parsed form in `offer_json` is the one used.         |
| `updated_at`             | When the row last changed.                                                                                                                |

### `interview_asked` — what the interview has already put to you

`prompt` (primary key, whitespace-normalized), `topic`, `asked_at`.

The interview computes its next question from the profile as it stands, every time
it is opened; nothing about the question is stored ahead of time. This table holds
the one thing that has to survive between visits: what was already asked, or the
model would start over from the same first gap every time. A question you skipped is
remembered like any other, which is what keeps it from coming back.

`profiler.store.forget_questions()` empties it, and a fresh CV import calls exactly
that: the questions were written for a profile that no longer exists.
