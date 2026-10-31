# Read-only discussion review CLI

**Current focus: topic-based search discovery only.** Use
`python -m bot.discover --config discovery.example.json` for the separate official
search-API experiment. It uses no Reddit requests, LLM or video sync. See
[discovery-experiment.md](docs/discovery-experiment.md) for credentials, limits,
live-validation status, and the distinction between finding five links and
verifying five recently published threads. Unknown thread states are retained
in this experiment instead of dropping all search results.

This project discovers public discussion candidates and prepares local review reports.
It implements **no Reddit account actions**: no login, comments, messages, votes,
Telegram bot, or automatic publishing. The previous publishing pipeline has been
replaced. No `.env` file is loaded or read by this application.

**Current limitation:** end-to-end Reddit discovery is blocked. A direct request
to `https://www.reddit.com/robots.txt` during implementation returned HTTP 200 and
`User-agent: *` / `Disallow: /`. No discussion page was fetched by the application.
Search/browser indexes can show different snapshots; they are not authorization
for this client. See [access-check.md](docs/access-check.md).

The scraper does not bypass this restriction. Automated discovery remains the
required feature; fixture demonstrations are explicitly labeled and are not a
manual-discovery substitute or a claim of successful live discovery.

## Setup

Use Python 3.10+ (3.11+ recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `config.json` from `config.example.json` and replace the clearly labeled
**sample** video with actual title, URL, transcript or factual summary, and topic
keywords. The sample is not a verified Forkitopen video. No YouTube credentials
are needed: this version uses local video input as requested.

Configuration includes optional target subreddits, maximum age/results, total
HTTP requests, pacing, bounded retries, an access-permission reference, and an
optional LLM. An empty subreddit list selects the public `/new/` listing. This
does not bypass blocked subreddit/search endpoints.

## One-shot use

```bash
python -m bot --config config.json --output reports
```

The CLI writes `reports/review.md` and `reports/review.json`, then exits. No server
is needed. Exit code 2 indicates blocked/partial-blocked access, not a successful
empty search. A robots request is allowed as the preliminary access check; no
content requests proceed without applicable robots permission, a documented
permission reference, and a parser verified against authorized actual HTML.

Do not invent a permission reference. Supplying one is not a permission grant,
does not override robots.txt, and cannot enable the unverified parser. Technical
accessibility is not interpreted as permission.

## Parser status and architecture

The existing repository was a small `bot` Python package; that package and the
CLI convention are retained. The modules are:

```text
bot/
  __main__.py   CLI, selection/report orchestration
  models.py     local JSON configuration and discussion records
  access.py     GET-only access checks, pacing, limits, in-memory cache
  discovery.py  DiscoveryAdapter protocol and public-page adapter
  parsing.py    provisional semantic HTML parser
  selection.py  conservative evidence/age/status filtering
  drafting.py   optional grounded English drafts
  reporting.py  Markdown and JSON reports
  state.py      hashed deduplication and Retry-After deadline
tests/
  fixtures/discussion.html  explicitly synthetic HTML
  test_review.py
config.example.json
```

Because the access rules prevented authorized raw-HTML inspection, **no production
Reddit DOM selectors have been verified**. The provisional parser reads standard
HTML anchors and `DiscussionForumPosting` JSON-LD in synthetic fixtures only.
It does not assert that Reddit currently emits this structure. The parser's
`verified_live_structure` is false. If permission/robots conditions change,
authorized inspection and parser verification are still required; setting a
configuration string alone will not make the scraper work. Playwright was not
added because there was no permitted rendering investigation that justified it.

An approved Reddit API adapter can implement `DiscoveryAdapter.discover(config)`
and return the same `DiscoveryResult` and provenance-bearing discussion records.
No account-action capability belongs in this interface.

## Access handling

- Requests go only to `https://www.reddit.com`; no alternate domains, JSON/private
  endpoints, login cookies, stealth tools, CAPTCHA solving, or proxy rotation.
- Redirects are not followed. Environment proxy/auth settings are disabled.
  Cookies are cleared before every request. Only GET is implemented for Reddit.
- Total request limits include robots.txt and retries. Requests are paced at
  least three seconds apart (default five). Transient network/5xx retries are
  bounded; 401/403/451, challenges, unknown content and rate limits stop the source.
- Retry-After is persisted as a UTC deadline and never shortened to fit a wait
  cap. The source stops instead of waiting, including across subsequent runs.
- Successful public response caching is TTL-bounded and **in memory only**.
  Discussion HTML is not cached persistently. Changed/unsupported structure is
  reported as unknown, not filled with guessed content.

## Selection and optional LLM

Unknown publication dates, timezone-less dates, future dates, unknown thread
status, locked/archived threads, missing post text, and old discussions are
excluded. A candidate must look like a question and share substantive concepts
with an actual factual passage from the supplied video, not merely its keywords.
The deterministic gate is a conservative lexical prefilter, **not a semantic
proof of relevance**. Each decision is explained. With an LLM enabled, it separately
judges whether the material can answer this specific question.

The default is `"provider": "none"`. Discovery is independently testable and
does not call an LLM. Potential matches are reported without generated replies
and explicitly marked as needing semantic review. No fabricated draft is supplied.

For local drafting, install [Ollama](https://ollama.com/download), start it, and:

```bash
ollama pull qwen3:4b
```

Set the local JSON configuration:

```json
"llm": {
  "provider": "ollama",
  "model": "qwen3:4b",
  "max_calls": 5,
  "max_text_chars": 6000
}
```

Only local `http://127.0.0.1:11434/api/chat` is used; there is no cloud fallback
or automatic retry. Model setup/download is not performed automatically.

For optional paid OpenAI use, install `openai>=2,<3`, export `OPENAI_API_KEY` in
your shell, configure `provider=openai` and an accessible structured-output model,
and explicitly pass `--allow-paid-llm`. An API key alone cannot activate calls.
The configured call count is a per-run count limit, not a dollar budget guarantee.
Existing `.env` credentials from the previous version are not loaded.

### What is sent to the LLM?

Only the discussion title, up to `max_text_chars` of public post text, video title,
and bounded selected factual video passages. No author names, profile data,
account identifiers, cookies, or credentials from the old pipeline are collected.
Public text itself can still contain personal information. This data boundary is
printed before discovery and included in both reports. Ollama processes it locally;
OpenAI transmits it to a remote provider. The first adapter does not collect comments
because there is no verified accessible structure or demonstrated need for them.

Each generated sentence must cite a verbatim supporting quote from the video
material. Missing quotes reject the draft. **A quote match cannot prove entailment**;
human review must confirm each claim. Unsupported/irrelevant questions are skipped
with a reason. Reddit text is explicitly untrusted input in the system prompt.

Replies contain no promotional links or ownership claims. Video URLs appear only
as review references. Community rules remain unverified, so the application does
not suggest adding links. The operator's known role is channel promoter, not
necessarily video creator; it never falsely says "I made this video." There is
no automated publication step.

## Reports and minimal state

Reports contain discussion metadata, field provenance, collection time, matched
video, selection explanation, optional reply/evidence, unresolved rule checks,
access errors, and skipped entries. Dates are labeled **page-declared with a
timezone**, not independently confirmed publication facts. Unknowns stay unknown.

Raw discussion bodies, author profiles and comments are not stored in reports
or state. Responses exist transiently in memory. Persistent deduplication stores
only SHA-256 hashes of canonical discussion URL/video pairs for completed drafts;
temporary LLM failures or LLM-disabled candidates can be retried later. A small
metadata entry stores any Retry-After deadline. This data uses a separate
`data/review-state.sqlite3`, not the previous bot database. Old data is neither
opened nor deleted by the new application. Protect/delete old files yourself
according to your retention needs.

Reports are written with owner-only permissions and dedup state commits only
after both reports are written. Report files are replaced on each run; use a
different `--output` directory if you need distinct review batches.

## Fixture tests versus live discovery

```bash
python -m unittest discover -s tests -v
python -m compileall -q bot tests
python -m bot --fixture-demo tests/fixtures/discussion.html --output reports/fixture-demo
```

The demonstration does not contact Reddit. It parses explicitly synthetic HTML
and labels the report `synthetic_fixture` / `fixture_only`. Sample dates can
become too old and will then be filtered; tests use controlled time where needed.
Tests cover parsing/provenance, unknown dates, relevance evidence, status/age,
deduplication, robots denial, access denial, CAPTCHA, retries, pacing/cache and
Retry-After persistence. Mocked/fixture success does not demonstrate live scraping.

## References

- [Direct robots policy](https://www.reddit.com/robots.txt)
- [Reddit public content policy](https://support.reddithelp.com/hc/en-us/articles/26410290525844-Public-Content-Policy)
- [Reddit responsible builder policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
- [Ollama chat API](https://docs.ollama.com/api/chat)
