# jap: job application pipeline

Job links in, tailored CV and cover letter out.

You paste a job link. jap reads the posting, picks the best of your base CVs, has an LLM reorder and
lightly rephrase it for that job, writes a cover letter and drafts answers to the application form's
questions. It then checks all of it against a profile of facts you wrote yourself. You get a folder with a
Word and PDF CV, a cover letter and a report. jap never sends anything: you review it and apply yourself.

The core rule is that **nothing reaches a CV unless your profile says it**. The LLM may choose, order and
lightly reword your verified bullets. A plan is rejected when it:

- names a technology, employer or number that your profile doesn't state,
- uses a skill that isn't on your allow-list,
- mentions an employer you marked as hidden,
- uses a stock phrase ("passionate", "fast-paced", …) or goes over the length limits.

A rejected plan is retried once with the errors attached. If it still fails, the job is flagged for review
instead of producing a CV.

## How it works

```
posting URL / pasted text
  → scrape        Greenhouse, Lever and Ashby APIs, JSON-LD, then the readable page text
  → extract       LLM → structured posting (must-haves, keywords, location, salary, …)
  → select track  keyword scoring picks one of your base CVs (backend, fullstack, …)
  → tailor        LLM → plan: headline, summary, skill groups, bullet choice, cover letter
  → validate      deterministic checks against your profile; one retry with the errors
  → render        docx templates → PDF (LibreOffice), page limits enforced (CV 2, letter 1)
  → answers       LLM drafts answers to the form's questions from your stated facts
```

Each job lands in `applications/<date>_<id>_<company>_<role>/`:

| File                       | What                                                                       |
| -------------------------- | -------------------------------------------------------------------------- |
| `<Name> <Track>_<Co>_CV.*` | Tailored CV, docx and PDF                                                  |
| `<Name> Cover_Letter_<Co>.*` | Cover letter, docx and PDF                                               |
| `match_report.md`          | Read this first: validation result, track choice, keyword coverage, gaps, cost |
| `answers.md`               | Draft answers to the form's questions, plus anything that needs your input |
| `plan.json`, `job.json`    | The tailoring plan and the extracted posting                               |
| `source/`, `run.log`       | Raw scraped page and the step-by-step log                                  |
| `VALIDATION_FAILED.md`     | Only when the plan still failed validation after the retry                 |

`applications/tracker.md` lists every job by status.

## Requirements

- Docker with Compose. The container includes LibreOffice and the fonts needed for PDF output.
- An [Anthropic API key](https://console.anthropic.com/), or any OpenAI-compatible endpoint (OpenAI,
  OpenRouter, a local Ollama, …).
- Optional, for development: [uv](https://docs.astral.sh/uv/) and Python 3.12+. Without Docker you get
  docx output only, unless LibreOffice is installed on the host.

A job uses roughly 10–20k input and 15–20k output tokens across its LLM calls. That is about $0.40 with the
default Claude Opus 5.5, and much less with a cheaper model. A monthly budget cap pauses new jobs once it
is reached (`MONTHLY_BUDGET_USD`, default $10), and each `match_report.md` shows what the job cost.

## Quick start

```sh
git clone https://github.com/Jendorski/job-applications-pipeline.git
cd job-applications-pipeline

cp .env.example .env               # set ANTHROPIC_API_KEY
docker compose build
```

Then create `profile/` from the fictional example candidate, John Doe, either way:

```sh
uv run jap init                                  # on the host (needs uv)
docker compose run --rm pipeline jap init        # or inside the container (Docker only)
```

`jap init` copies `examples/profile/` to `profile/` and creates the `inbox/`, `applications/` and `data/`
folders. If `profile/` already has files in it, it leaves them alone, so running it twice is safe. On
Linux, run `mkdir -p profile inbox applications data` before the Docker version. Otherwise Docker creates
those folders as root and the container can't write to them.

Try it out:

```sh
# Render the bundled sample plan without calling an LLM:
docker compose run --rm pipeline jap render fixtures/sample_plan.json --out applications/test

# Run one real job end to end:
docker compose run --rm pipeline jap apply https://job-boards.greenhouse.io/<company>/jobs/<id>
```

Once that works, replace the example profile with your own (next section). `profile/`, `.env`,
`applications/`, `inbox/` and `data/` are gitignored, so your personal data stays out of the repo.

## Your profile

Everything jap knows about you lives in two YAML files in `profile/`. Start from the copy that `jap init`
made of `examples/profile/` and replace every fact. Commands that need a profile stop with a pointer to
`jap init` if `profile/` is missing.

**`profile/master_profile.yaml`** holds every fact that may appear on a CV:

- `person`: name, email, links and `career_start` (used to compute "N+ years").
- `skills`: your canonical skill names, grouped. This is the allow-list: a skill that isn't here can't
  appear in a skill group. `aliases` map other spellings to canonical names (e.g. `Postgres: PostgreSQL`).
- `experience`: roles, newest first, each with bullets. A bullet has an `id` (`<role_id>.<name>`), `text`,
  `skills`, a `priority` (1 = always include, 3 = filler) and a `status`. Only `status: verified` bullets are
  ever used. `show_on_cv: false` keeps a role off CVs: its skills still count, but its company name is
  blocked from all generated text.
- `projects` and `education`. A project with `hobby: true` is only offered for games and creative-tech roles.
- `candidate`: facts for application forms (based in, relocation, work authorisation, salary, start date)
  and a motivation in your own words. Answers are built from these and nothing else.
- `never_claim`: terms that fail validation wherever they appear in generated text.
- `unverified.devops_cv` (optional): employers and skills from an older CV that you don't want used. Those
  employers are blocked, and those skills are rejected in generated text.

**`profile/tracks.yaml`** holds your base CVs ("tracks"). Each one has a `label` (used in filenames),
`headlines` the tailor may choose from, a `summary` (`{years}` is filled in), `skill_groups`, the bullet ids
to show for each role, `projects` and `keywords` used to pick the track. The example has `backend`,
`fullstack`, `industrial_iot` and `fintech_web3`. Rename or replace them as you like.

Check your work after every edit:

```sh
docker compose run --rm pipeline jap profile check      # or: uv run jap profile check
docker compose run --rm pipeline jap render-track --out applications/tracks   # see each base CV
```

## Daily use: the watcher

```sh
docker compose up -d          # start the watcher; it restarts with Docker after that
```

1. Paste links into `inbox/links.txt`, one job per block with a blank line between jobs, and save. Extra
   URLs in a block are company or mission pages that help the cover letter. Optional lines in a block:
   `# track: backend`, `# note: referred by Ada`, `# force` (redo an existing job).
2. Within about 30 seconds the watcher picks up the links and removes them from the file.
   `inbox/processed.log` shows each step.
3. Review `applications/<folder>/` (read `match_report.md` first), then apply yourself.
4. Record what happened in `inbox/status.txt`, e.g. `7 applied` or `7 rejected no visa sponsorship`.
   Statuses: applied, interviewing, offer, rejected, withdrawn, skipped.

Sites that block scraping (LinkedIn, Indeed): save the description as `inbox/manual/<anything>.md`. It can
start with header lines such as `url: …`, `track: …` and `note: …`.

If you keep `links.txt` open in an editor, save right after pasting. An unsaved buffer saved later can
bring back links the watcher already took. They're then skipped as duplicates, so nothing runs twice.

Once a day the watcher deletes application folders older than `APPLICATIONS_KEEP_DAYS`. It keeps the
folders of jobs marked applied, interviewing or offer.

```sh
docker compose logs -f        # watch it work
docker compose ps             # should say (healthy)
docker compose down           # stop (the current job finishes first)
docker compose up -d --build  # after changing the Dockerfile or dependencies
docker compose restart        # after changing code in src/
```

## Commands

Every command runs in the container (`docker compose run --rm pipeline jap …`) or on the host with uv
(`uv run --env-file .env jap …`, docx only unless LibreOffice is installed).

```sh
jap init                               # create profile/ from examples/profile/ (never overwrites)
jap apply <posting-url> [<company/mission page> …] [--track backend] [--note "…"] [--force]
jap apply --jd-file pasted.md          # pasted description, no scraping
jap mark 7 applied [--note "…"]        # applied | interviewing | offer | rejected | withdrawn | skipped
jap tracker                            # print applications/tracker.md
jap usage                              # tokens and USD per month and model
jap cleanup --dry-run                  # folders the daily cleanup would delete
jap profile check                      # lint profile/master_profile.yaml and tracks.yaml
jap render <plan.json> --out <dir>     # render a TailorPlan without calling the LLM
jap render-track [<track> …] --out <dir>   # untailored base CV per track
jap watch                              # the watcher loop (what `docker compose up` runs)
```

Add `--no-pdf` to `apply`, `render` or `render-track` to skip PDF conversion.

## Configuration

Settings come from `.env`. [`.env.example`](.env.example) documents every option. The main ones:

| Variable                        | Default           | What                                                   |
| ------------------------------- | ----------------- | ------------------------------------------------------ |
| `LLM_PROVIDER`                  | `anthropic`       | `anthropic` or `openai_compat`                         |
| `ANTHROPIC_API_KEY`             |                   | Needed for `anthropic`                                 |
| `MODEL_EXTRACT`, `MODEL_TAILOR` | `claude-opus-5-5` | Models for posting extraction and tailoring            |
| `EFFORT_EXTRACT`, `EFFORT_TAILOR` | `low`, `medium` | Reasoning effort per step                              |
| `MONTHLY_BUDGET_USD`            | `10`              | New jobs pause once this month's spend reaches it      |
| `APPLICATIONS_KEEP_DAYS`        | `30`              | Daily cleanup age; `0` turns cleanup off               |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` |               | For `openai_compat` (set both model names too)         |
| `LLM_PRICES`                    | Claude built in   | JSON prices for other models, so the budget cap works  |

The prompts are in [`src/jap/prompts/`](src/jap/prompts/).

## Templates

`templates/cv.docx` and `templates/cover_letter.docx` are docxtpl (Jinja) templates. They are generated,
so don't edit them by hand. To use your own look, rebuild them from a CV and cover letter you made by hand:

```sh
uv run python scripts/make_templates.py --cv "path/to/My CV.docx" --letter "path/to/My Cover Letter.docx"
```

The script copies the formatting of particular paragraphs in your documents. Their positions are set in
`CV_PROTOS` and `LETTER_PROTOS` in [`scripts/make_templates.py`](scripts/make_templates.py), so adjust those
indexes to match your documents' paragraph order.

The container renders PDFs with EB Garamond in place of Garamond
([`docker/fontconfig/`](docker/fontconfig/)).

## Development

```sh
uv sync
uv run pytest
```

The tests use the fictional profile in `examples/profile/`, the plan in `fixtures/sample_plan.json` and
saved job-board responses in `tests/fixtures/scrape/`, with a fake LLM provider. They need no API key and
no network.

`./src` is mounted into the container, so code changes only need `docker compose restart`. Changes to the
Dockerfile or dependencies need a rebuild.

## Layout

| Path                          | What                                                                       |
| ----------------------------- | -------------------------------------------------------------------------- |
| `profile/`                    | Your master profile and tracks (gitignored; created by `jap init`)         |
| `examples/profile/`           | Fictional example profile and tracks; copied by `jap init`, used by the tests |
| `fixtures/`                   | Hand-written TailorPlans for `jap render` and the tests                     |
| `templates/`                  | Generated docx templates for the CV and cover letter                       |
| `src/jap/`                    | `pipeline.py` (one job end to end), `watcher.py`, `scrape/`, `extract.py`, `select_track.py`, `tailor.py`, `validate.py`, `answers.py`, `render/`, `cli.py` |
| `src/jap/prompts/`            | LLM prompts                                                                |
| `inbox/`                      | Links, pasted descriptions and status updates for the watcher (gitignored) |
| `applications/`               | Output, one folder per job (gitignored)                                    |
| `data/`                       | SQLite database, logs, health heartbeat (gitignored)                       |
