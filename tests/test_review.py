import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests
import httpx

from bot.__main__ import build_report
from bot.access import AccessStopped, PublicClient, Robots
from bot.discovery import PublicPageAdapter
from bot.drafting import Drafter
from bot.models import Config, DiscoveryResult, LLMConfig, RequestLimits
from bot.parsing import SemanticHTMLParser, canonical_url, parse_date
from bot.reporting import write_reports
from bot.selection import assess
from bot.state import State

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / 'tests/fixtures/discussion.html'
URL = 'https://www.reddit.com/r/explainlikeimfive/comments/fixture01/'
NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def config():
    return Config.model_validate_json((ROOT / 'config.example.json').read_text())


def discussion():
    return SemanticHTMLParser().discussion(FIXTURE.read_text(), URL)


def response(status=200, text='User-agent: *\nAllow: /', headers=None):
    result = Mock(status_code=status)
    result.headers = {'Content-Type': 'text/plain', **(headers or {})}
    result.iter_content.return_value = [text.encode()]
    return result


class ParserTests(unittest.TestCase):
    def test_synthetic_structure_and_field_provenance(self):
        d = discussion()
        self.assertEqual(d.url, URL)
        self.assertEqual(d.subreddit, 'explainlikeimfive')
        self.assertIs(d.locked, False)
        self.assertIsNotNone(d.published_at.tzinfo)
        self.assertEqual(d.sources['comments'], 'not collected')
        self.assertIn('datePublished', d.sources['published_at'])

    def test_links_are_canonical_deduplicated_and_same_origin(self):
        self.assertEqual(SemanticHTMLParser().links(FIXTURE.read_text()), [URL])
        self.assertIsNone(canonical_url('https://www.reddit.com.evil.example/r/a/comments/b/'))
        self.assertIsNone(canonical_url('/r/a/comments/b/title/commentid/'))

    def test_missing_and_naive_dates_stay_unknown(self):
        self.assertIsNone(parse_date(None))
        self.assertIsNone(parse_date('2026-10-31T12:00:00'))
        self.assertIsNone(parse_date('3 hours ago'))

    def test_unrecognized_dynamic_html_is_not_fabricated(self):
        self.assertIsNone(SemanticHTMLParser().discussion('<html><div id="app"></div></html>', URL))
        self.assertFalse(SemanticHTMLParser.verified_live_structure)


class SelectionTests(unittest.TestCase):
    def test_video_passage_supports_question(self):
        matched, explanation, evidence = assess(discussion(), config().videos[0], 7, NOW)
        self.assertTrue(matched)
        self.assertTrue(evidence)
        self.assertIn('not semantic verification', explanation)

    def test_keywords_alone_do_not_supply_evidence(self):
        video = config().videos[0].model_copy(update={
            'transcript_or_summary': 'Baking sourdough bread requires a mature starter and appropriate oven temperatures.'})
        self.assertFalse(assess(discussion(), video, 7, NOW)[0])

    def test_old_locked_archived_and_unknown_are_excluded(self):
        for name, value in [('locked', True), ('archived', True), ('locked', None),
                            ('published_at', None), ('published_at', NOW-timedelta(days=8))]:
            with self.subTest(name=name, value=value):
                d = discussion()
                setattr(d, name, value)
                self.assertFalse(assess(d, config().videos[0], 7, NOW)[0])


class StateAndReportTests(unittest.TestCase):
    def test_dedup_does_not_store_raw_content(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'state.sqlite3'
            state = State(path)
            self.assertFalse(state.seen(URL, 'video'))
            state.stage(URL, 'video')
            self.assertTrue(state.seen(URL, 'video'))
            state.commit()
            state.close()
            state = State(path)
            self.assertTrue(state.seen(URL, 'video'))
            self.assertFalse(state.seen(URL, 'other-video'))
            state.close()
            self.assertNotIn(b'fixture01', path.read_bytes())

    def test_fixture_report_is_explicit_and_no_llm_or_raw_post_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            state = State(Path(temp)/'state.sqlite3')
            d = discussion()
            d.published_at = datetime.now(timezone.utc)
            cfg = config()
            report = build_report(cfg, DiscoveryResult([d, d], source_status='fixture_only'),
                                  state, Drafter(cfg.llm), 'synthetic_fixture')
            self.assertEqual(len(report['reviews']), 1)
            self.assertIsNone(report['reviews'][0]['suggested_reply'])
            self.assertIn('Duplicate', report['skipped'][0]['reason'])
            write_reports(report, temp)
            content = (Path(temp)/'review.json').read_text()
            self.assertNotIn(d.post_text, content)
            self.assertEqual(json.loads(content)['run_type'], 'synthetic_fixture')
            state.close()


class DraftTests(unittest.TestCase):
    def call(self, claims):
        cfg = LLMConfig(provider='ollama', model='qwen3:4b', max_calls=1)
        drafter = Drafter(cfg)
        quote = 'GPS satellites broadcast timing signals.'
        answer = dict(relevant=True, can_answer=True, explanation='Timing signals answer this question.', claims=claims)
        response = httpx.Response(200, json={'message': {'content': json.dumps(answer)}},
                                  request=httpx.Request('POST', 'http://127.0.0.1:11434/api/chat'))
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=None)
        client.post.return_value = response
        with patch('bot.drafting.httpx.Client', return_value=client):
            result = drafter.draft(discussion(), config().videos[0], [quote])
        return result, drafter, client

    def test_grounded_draft_and_bounded_minimal_payload(self):
        result, drafter, client = self.call([dict(sentence='GPS satellites send timing signals.',
                                                evidence_quote='GPS satellites broadcast timing signals.')])
        self.assertIsNotNone(result[0])
        payload = client.post.call_args.kwargs['json']
        data = json.loads(payload['messages'][1]['content'])
        self.assertNotIn('url', data)
        self.assertNotIn('comments', data)
        self.assertIn('untrusted data', payload['messages'][0]['content'])
        self.assertIn('call limit', drafter.draft(discussion(), config().videos[0], ['passage'])[1])

    def test_unsupported_quote_rejects_draft(self):
        result, _, _ = self.call([dict(sentence='The receiver uses five atomic clocks.',
                                      evidence_quote='An invented factual passage with no source.')])
        self.assertIsNone(result[0])
        self.assertIn('supporting quote', result[1])

    def test_promotional_link_rejects_draft(self):
        result, _, _ = self.call([dict(sentence='Watch https://example.com',
                                      evidence_quote='GPS satellites broadcast timing signals.')])
        self.assertIsNone(result[0])
        self.assertIn('unapproved link', result[1])

    def test_paid_provider_is_not_enabled_by_credentials_alone(self):
        with self.assertRaisesRegex(ValueError, '--allow-paid-llm'):
            Drafter(LLMConfig(provider='openai', model='example'))


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.temp.name)/'state.sqlite3')
        self.session = requests.Session()
        self.session.get = Mock()
        self.sleep = Mock()
        self.client = PublicClient(RequestLimits(), self.state, self.session, self.sleep, clock=lambda: 1000)

    def tearDown(self):
        self.client.close()
        self.state.close()
        self.temp.cleanup()

    def test_robots_denial_requests_no_listing(self):
        self.session.get.return_value = response(text='User-agent: *\nDisallow: /')
        result = PublicPageAdapter(self.client).discover(config())
        self.assertEqual(result.source_status, 'blocked')
        self.assertEqual(result.errors[0]['code'], 'robots_disallowed')
        self.session.get.assert_called_once()
        self.assertTrue(self.session.get.call_args.args[0].endswith('/robots.txt'))

    def test_permission_is_not_inferred_from_robots_allow(self):
        self.session.get.return_value = response()
        result = PublicPageAdapter(self.client).discover(config())
        self.assertEqual(result.errors[0]['code'], 'permission_unverified')
        self.session.get.assert_called_once()

    def test_access_denied_stops_further_requests(self):
        self.session.get.return_value = response(status=403)
        with self.assertRaises(AccessStopped) as first:
            self.client.load_robots()
        self.assertEqual(first.exception.code, 'access_denied')
        with self.assertRaises(AccessStopped):
            self.client.load_robots()
        self.session.get.assert_called_once()

    def test_captcha_stops_source(self):
        self.session.get.return_value = response(text='<html>Verify you are human</html>')
        with self.assertRaises(AccessStopped) as caught:
            self.client.load_robots()
        self.assertEqual(caught.exception.code, 'challenge')

    def test_retry_after_persists_across_runs_without_shortening(self):
        self.session.get.return_value = response(status=429, headers={'Retry-After': '3600'})
        with self.assertRaises(AccessStopped):
            self.client.load_robots()
        self.assertEqual(float(self.state.get('reddit_not_before')), 4600)
        second = PublicClient(RequestLimits(), self.state, self.session, self.sleep, clock=lambda: 1100)
        with self.assertRaises(AccessStopped) as caught:
            second.load_robots()
        self.assertEqual(caught.exception.code, 'retry_after_active')
        self.session.get.assert_called_once()

    def test_memory_cache_and_pacing(self):
        self.session.get.return_value = response()
        self.client.load_robots()
        self.client.get('https://www.reddit.com/new/')
        self.client.get('https://www.reddit.com/new/')
        self.assertEqual(self.session.get.call_count, 2)
        self.sleep.assert_called_once_with(5)
        self.assertFalse(self.session.get.call_args.kwargs['allow_redirects'])

    def test_bounded_retries_and_total_request_limit(self):
        self.session.get.return_value = response(status=500)
        with self.assertRaises(AccessStopped):
            self.client.load_robots()
        self.assertEqual(self.session.get.call_count, 2)
        self.assertEqual(self.client.count, 2)

    def test_robots_wildcards_and_specific_allow(self):
        robots = Robots('User-agent: *\nDisallow: /*/search*\nAllow: /r/a/search/help$')
        self.assertFalse(robots.allowed('https://www.reddit.com/r/a/search/?q=gps'))
        self.assertTrue(robots.allowed('https://www.reddit.com/r/a/search/help'))


if __name__ == '__main__':
    unittest.main()
