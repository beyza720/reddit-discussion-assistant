# Topic discovery is the current acceptance gate

Run only link discovery before investing in drafting or video synchronization.
The separate CLI uses the official Brave Web Search API and never requests a
Reddit page. It does not import or invoke an LLM. Example topic data is GPS;
this has not been established as a real Forkitopen video's topic.

```bash
source .venv/bin/activate
python -m bot.discover --config discovery.example.json
```

Without explicit API authorization this writes a blocked report and makes zero
requests. To execute, obtain a Brave Search API subscription/key, check its price
and usage limits, export `BRAVE_SEARCH_API_KEY`, and run:

```bash
python -m bot.discover --config discovery.example.json --allow-search-api
```

Do not paste credentials in chat. Existing `.env` is not read. The flag authorizes
at most `max_requests` API calls (hard maximum three), not an unlimited workload.
There is no automatic retry or fallback provider. Provider charges may apply.

Input: natural-language topic, keywords, optional subreddits, age filter and result
limit. Queries include `site:reddit.com`, subreddit scope where provided, and the
topic. No general `/new/` listing is used. Results are normalized to discussion
URLs, deduplicated, screened for keyword evidence in titles/snippets, and ranked
to prefer question-like titles. This is candidate discovery, not video-content
relevance verification. No full post text is fetched or retained.

Unknown locked/archived states do **not** eliminate search candidates. They remain
explicit unknowns with review requirements. Titles, URLs and provider date metadata
are retained; snippets are used transiently for lexical screening, not saved.

Outputs: `reports/discovery/discovery.md` and `discovery.json`.

- Exit 0: the requested number of candidate links was found.
- Exit 3: fewer candidate links were found.
- Exit 2: missing credentials/authorization, blocked or errored API access.
- Even exit 0 does **not** verify five recent Reddit publications. Brave documents
  freshness/page_age as publication OR modification dates. The report always
  distinguishes candidate count from verified publication freshness.

## Live evidence and remaining limitation

The assistant's connected search tool automatically found real GPS-related Reddit
links. This was not the local Brave adapter and must not be counted as its live
validation. A seven-day search filter still returned old discussions, including
July 2026 and October 18, 2026. Search recency alone is not a publication guarantee.
No Reddit page was fetched by the local CLI.

`BRAVE_SEARCH_API_KEY` was not configured in the execution environment during
implementation. No `.env` credentials were inspected. Therefore the standalone
Brave CLI's real result count remains **unverified**, and the acceptance criterion
of five relevant, current Reddit threads remains unmet. Mocked API tests do not
change that conclusion. No paid API call was made on the user's behalf.

## References

- [Official endpoint and authentication](https://api-dashboard.search.brave.com/api-reference/web/search/get)
- [API dashboard](https://api-dashboard.search.brave.com/)
