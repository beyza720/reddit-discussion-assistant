"""Discovery-only experiment using the official Brave Web Search API.

No Reddit URL is fetched. Search metadata is not verified publication metadata.
This module deliberately does not import drafting, video sync or account clients.
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, model_validator

from .parsing import canonical_url
from .selection import tokens

ENDPOINT = 'https://api.search.brave.com/res/v1/web/search'


class SearchConfig(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    keywords: list[str] = Field(min_length=1, max_length=15)
    subreddits: list[str] = Field(default_factory=list, max_length=3)
    max_age_days: int = Field(default=7, ge=1, le=365)
    max_results: int = Field(default=5, ge=1, le=20)
    max_requests: int = Field(default=3, ge=1, le=3)

    @model_validator(mode='after')
    def names(self):
        if any(not re.fullmatch(r'[A-Za-z0-9_]{2,30}', s) for s in self.subreddits):
            raise ValueError('Invalid subreddit name')
        # Inputs are natural-language topics, not arbitrary query operators.
        if re.search(r'[:"()\n\r]', self.topic):
            raise ValueError('Use a natural-language topic without search operators')
        return self


def queries(config):
    scopes = [f'site:reddit.com/r/{s}/comments/' for s in config.subreddits] or ['site:reddit.com/r/']
    variants = [config.topic, config.topic + ' how does it work', config.topic + ' question']
    result = []
    for variant in variants:
        for scope in scopes:
            result.append(f'{scope} {variant}')
    return result[:config.max_requests]


def plain(value):
    return BeautifulSoup(str(value or ''), 'html.parser').get_text(' ', strip=True)[:1500]


def candidate(item, config, query):
    original = str(item.get('url') or '')
    # The API may return the ordinary non-www spelling of the same public site.
    original = re.sub(r'^https://reddit\.com/', 'https://www.reddit.com/', original)
    url = canonical_url(original)
    if not url:
        return None, 'Not a supported Reddit discussion URL.'
    subreddit = url.split('/')[4]
    if config.subreddits and subreddit.lower() not in {s.lower() for s in config.subreddits}:
        return None, 'Outside the configured subreddits.'
    title = plain(item.get('title'))[:500]
    snippet = plain(item.get('description'))
    if not title or title.lower() in ('[removed]', '[deleted]'):
        return None, 'Missing or removed title.'
    overlap = tokens(title + ' ' + snippet) & tokens(' '.join(config.keywords))
    if len(overlap) < min(2, len(tokens(' '.join(config.keywords)))) or not overlap:
        return None, 'Insufficient topic evidence in the search title/snippet.'
    question = bool(re.search(r'\?|\b(how|why|what|explain|eli5|help|understand)\b', title, re.I))
    return dict(title=title, url=url, subreddit=subreddit,
        relevance_explanation='Search title/snippet contains: ' + ', '.join(sorted(overlap)) +
            '. Candidate-level lexical relevance only; post content has not been verified.',
        question_like_title=question, search_page_date=item.get('page_age'),
        search_date_meaning='Provider-reported publication OR modification date; not verified Reddit publication time.',
        publication_date=None, publication_date_verified=False,
        freshness_status='unverified', locked=None, archived=None,
        review_required=['Verify publication date and whether this thread is open.',
                         'Verify actual question context, relevance and community rules.'],
        field_source='Brave Web Search API web.results', discovery_query=query,
        topic_overlap_count=len(overlap)), None


def search(config, api_key, allow_api=False, session=None, sleep=time.sleep):
    report = dict(source='brave_web_search_api', topic=config.topic,
        collected_at=datetime.now(timezone.utc).isoformat(),
        status='not_started', requests_made=0, queries=[], candidates=[], skipped=[], errors=[],
        requested_results=config.max_results, requested_max_age_days=config.max_age_days,
        target_candidate_count_met=False, five_recent_publications_verified=False,
        limitation='Search links only. No Reddit pages, post bodies or account actions are requested. Search freshness is not publication verification.')
    if not allow_api:
        report['status'] = 'blocked'
        report['errors'].append('Search API requests require --allow-search-api; provider charges may apply.')
        return report
    if not api_key:
        report['status'] = 'blocked'
        report['errors'].append('BRAVE_SEARCH_API_KEY is missing. No request was made; .env is not loaded.')
        return report
    own_session = session is None
    session = session or requests.Session()
    session.trust_env = False
    now = datetime.now(timezone.utc)
    freshness = f'{(now-timedelta(days=config.max_age_days)).date().isoformat()}to{now.date().isoformat()}'
    seen = set()
    try:
        for query in queries(config):
            if report['requests_made']:
                sleep(3)
            report['queries'].append(query)
            report['requests_made'] += 1
            try:
                session.cookies.clear()
                response = session.get(ENDPOINT,
                    params={'q': query, 'count': 20, 'freshness': freshness,
                            'text_decorations': 'false', 'result_filter': 'web', 'search_lang': 'en'},
                    headers={'Accept': 'application/json', 'X-Subscription-Token': api_key},
                    timeout=20, allow_redirects=False)
            except requests.RequestException as exc:
                report['errors'].append(f'Search request failed ({type(exc).__name__}); no retry.')
                break
            try:
                if response.status_code != 200:
                    report['errors'].append(f'Search HTTP {response.status_code}; source stopped. Retry-After: {response.headers.get("Retry-After", "not supplied")}. No automatic retry.')
                    break
                data = response.json()
                items = (data.get('web') or {}).get('results', [])
                if not isinstance(items, list):
                    raise ValueError('Unsupported result schema')
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    result, reason = candidate(item, config, query)
                    if result is None:
                        report['skipped'].append({'reason': reason})
                        continue
                    if result['url'] in seen:
                        continue
                    seen.add(result['url'])
                    report['candidates'].append(result)
            except (ValueError, TypeError, AttributeError):
                report['errors'].append('Unsupported search API response; source stopped.')
                break
            finally:
                response.close()
            if len(report['candidates']) >= config.max_results:
                break
        report['candidates'].sort(key=lambda c: (c['question_like_title'], c['topic_overlap_count']), reverse=True)
        report['candidates'] = report['candidates'][:config.max_results]
        report['target_candidate_count_met'] = len(report['candidates']) >= config.max_results
        report['status'] = ('partial_error' if report['candidates'] else 'blocked') if report['errors'] else (
            'candidates_found' if report['target_candidate_count_met'] else 'insufficient_candidates')
        return report
    finally:
        if own_session:
            session.close()


def save(report, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    lines = ['# Topic discovery experiment', '', 'Status: ' + report['status'], '',
             report['limitation'], '', 'Search topic: ' + report['topic'], '',
             f"Candidates: {len(report['candidates'])}/{report['requested_results']}",
             'Recent Reddit publication dates independently verified: no', '']
    for i, entry in enumerate(report['candidates'], 1):
        # Plain blocks prevent untrusted search titles from becoming Markdown markup.
        title = entry['title'].replace('```', "'''")
        lines += [f'## Candidate {i}', '', '```text', title, '```', entry['url'], '',
                  entry['relevance_explanation'], '',
                  'Locked/archive/date/content: unknown; review required.', '']
    lines += ['## Errors', ''] + report['errors']
    for name, content in [('discovery.json', json.dumps(report, indent=2, ensure_ascii=False)),
                          ('discovery.md', '\n'.join(lines))]:
        path = output / name
        path.write_text(content, encoding='utf-8')
        path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description='Discover topic-specific Reddit links using an official search API; no LLM or Reddit requests.')
    parser.add_argument('--config', default='discovery.example.json')
    parser.add_argument('--output', default='reports/discovery')
    parser.add_argument('--allow-search-api', action='store_true', help='Explicitly authorize up to the configured search request count; provider charges may apply.')
    args = parser.parse_args()
    try:
        config = SearchConfig.model_validate_json(Path(args.config).read_text())
    except Exception:
        parser.exit(2, 'Invalid discovery configuration.\n')
    report = search(config, os.environ.get('BRAVE_SEARCH_API_KEY', ''), args.allow_search_api)
    save(report, args.output)
    print(f"Status: {report['status']}; candidates: {len(report['candidates'])}; API requests: {report['requests_made']}.")
    for error in report['errors']:
        print(error)
    print(f'Reports: {args.output}/discovery.md and discovery.json')
    return 0 if report['status'] == 'candidates_found' else 2 if report['errors'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
