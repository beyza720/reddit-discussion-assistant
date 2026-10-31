import argparse
from pathlib import Path

from .access import PublicClient
from .discovery import PublicPageAdapter
from .drafting import Drafter
from .models import Config, DiscoveryResult, utcnow
from .parsing import SemanticHTMLParser
from .reporting import write_reports
from .selection import assess
from .state import State


DISCLOSURE = ('With an LLM enabled, the selected discussion title and up to the configured '
              'max_text_chars of public post text, the video title, and bounded supporting '
              'video passages are sent to the configured provider. Usernames, profiles, cookies, '
              'and author identifiers are not collected. Participant text may still contain '
              'personal information: review the provider choice and applicable permissions. '
              'Ollama uses local 127.0.0.1:11434; OpenAI sends this context to its remote API. '
              'With provider=none, no LLM request is made.')


def build_report(config, discovery, state, drafter, run_type):
    report = dict(run_type=run_type, created_at=utcnow().isoformat(),
        source_status=discovery.source_status,
        limitation=('Fixture data is synthetic and does not demonstrate successful live discovery.'
                    if run_type == 'synthetic_fixture' else
                    'Live discovery is not verified. A blocked source must not be bypassed.'),
        llm_data_disclosure=DISCLOSURE, reviews=[], skipped=[], access_errors=discovery.errors)
    evaluated = set()
    for discussion in discovery.discussions:
        for video in config.videos:
            pair = (discussion.url, video.id)
            if pair in evaluated or state.seen(*pair):
                report['skipped'].append(dict(url=discussion.url, video_id=video.id, reason='Duplicate URL/video pair.'))
                continue
            evaluated.add(pair)
            matched, reason, evidence = assess(discussion, video, config.max_age_days)
            if not matched:
                report['skipped'].append(dict(url=discussion.url, video_id=video.id, reason=reason,
                    published_at=discussion.published_at.isoformat() if discussion.published_at else None))
                continue
            body, explanation, claims = drafter.draft(discussion, video, evidence)
            report['reviews'].append(dict(title=discussion.title, url=discussion.url,
                subreddit=discussion.subreddit,
                published_at=discussion.published_at.isoformat() if discussion.published_at else None,
                publication_date_status='page_declared_with_timezone', collected_at=discussion.collected_at.isoformat(),
                field_sources=discussion.sources, video_id=video.id, video_title=video.title,
                video_url=str(video.url), locked=discussion.locked, archived=discussion.archived,
                decision='draft_for_review' if body else 'candidate_without_draft',
                relevance_explanation=reason, draft_explanation=explanation, suggested_reply=body,
                claim_evidence=claims,
                community_rules='Unverified. Promotional link omitted. Verify AI-reply and participation rules before any manual use.',
                unresolved_questions=['Human review must confirm that each statement follows from its cited video passage.',
                                      'The model relevance decision is not independently verified.']))
            # Only a completed draft becomes persistently deduplicated. Failures/LLM-off
            # candidates can be evaluated on a later run; in-run duplicate candidates are suppressed.
            if body:
                state.stage(discussion.url, video.id)
            if len(report['reviews']) >= config.max_results:
                return report
    return report


def main():
    parser = argparse.ArgumentParser(description='Read-only Reddit discussion discovery and local review reports.')
    parser.add_argument('--config', default='config.example.json')
    parser.add_argument('--output', default='reports')
    parser.add_argument('--state', default='data/review-state.sqlite3')
    parser.add_argument('--fixture-demo', type=Path,
                        help='Explicit synthetic HTML demonstration; never a live discovery claim.')
    parser.add_argument('--allow-paid-llm', action='store_true')
    args = parser.parse_args()
    try:
        config = Config.model_validate_json(Path(args.config).read_text())
        drafter = Drafter(config.llm, allow_paid=args.allow_paid_llm)
    except Exception as exc:
        parser.exit(2, f'Configuration error: {type(exc).__name__}. Check the configuration and provider settings.\n')
    state = State(args.state)
    client = None
    try:
        print(DISCLOSURE)
        if args.fixture_demo:
            html = args.fixture_demo.read_text()
            url = 'https://www.reddit.com/r/explainlikeimfive/comments/fixture01/'
            discussion = SemanticHTMLParser().discussion(html, url)
            discovery = DiscoveryResult(discussions=[discussion] if discussion else [],
                source_status='fixture_only', errors=[] if discussion else [dict(
                    code='fixture_parse_error', url=str(args.fixture_demo), detail='Unsupported synthetic fixture.')])
            kind = 'synthetic_fixture'
        else:
            client = PublicClient(config.requests, state)
            discovery = PublicPageAdapter(client).discover(config)
            kind = 'live_attempt'
        report = build_report(config, discovery, state, drafter, kind)
        write_reports(report, args.output)
        state.commit()  # Commit dedup only after both reports were written successfully.
        print(f"Source status: {report['source_status']}; review entries: {len(report['reviews'])}.")
        for error in report['access_errors']:
            print(f"{error['code']}: {error['detail']}")
        print(f'Reports: {args.output}/review.md and {args.output}/review.json')
        return 2 if discovery.source_status in ('blocked', 'partial_blocked') else 0
    finally:
        if client:
            client.close()
        state.close()


if __name__ == '__main__':
    raise SystemExit(main())
