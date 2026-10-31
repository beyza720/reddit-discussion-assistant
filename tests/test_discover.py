import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

from bot.discover import ENDPOINT, SearchConfig, candidate, queries, save, search


def config():
    return SearchConfig(topic='how GPS satellite timing determines location',
                        keywords=['gps', 'satellite', 'timing', 'location'])


class DiscoveryTests(unittest.TestCase):
    def test_queries_are_topic_based_not_new_listing(self):
        result = queries(config())
        self.assertEqual(len(result), 3)
        self.assertTrue(all('site:reddit.com' in q and 'GPS' in q for q in result))

    def test_unknown_status_and_dates_are_retained_for_review(self):
        item = dict(url='https://www.reddit.com/r/askscience/comments/abc123/gps/',
                    title='How does GPS satellite timing work?', description='GPS satellite signals')
        result, reason = candidate(item, config(), 'query')
        self.assertIsNone(reason)
        self.assertIsNone(result['locked'])
        self.assertFalse(result['publication_date_verified'])
        self.assertEqual(result['freshness_status'], 'unverified')

    def test_no_key_or_no_permission_means_zero_requests(self):
        session = Mock()
        result = search(config(), '', True, session=session)
        self.assertEqual(result['requests_made'], 0)
        session.get.assert_not_called()
        result = search(config(), 'fake-key', False, session=session)
        self.assertEqual(result['requests_made'], 0)

    def test_five_links_are_not_five_verified_recent_posts(self):
        session = requests.Session()
        response = Mock(status_code=200)
        response.json.return_value = {'web': {'results': [dict(
            title=f'How does GPS satellite timing work? {i}',
            url=f'https://www.reddit.com/r/askscience/comments/abc{i}/gps/',
            page_age='2026-10-30T12:00:00') for i in range(5)]}}
        session.get = Mock(return_value=response)
        report = search(config(), 'fake-key', True, session=session)
        self.assertTrue(report['target_candidate_count_met'])
        self.assertFalse(report['five_recent_publications_verified'])
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], ENDPOINT)
        self.assertIn('freshness', session.get.call_args.kwargs['params'])
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
        session.close()

    def test_duplicates_and_off_topic_results_do_not_fill_target(self):
        session = requests.Session()
        response = Mock(status_code=200)
        response.json.return_value = {'web': {'results': [dict(
            title='How does GPS satellite timing work?',
            url='https://www.reddit.com/r/askscience/comments/abc/gps/')]*5 + [dict(
                title='What is sourdough?', url='https://www.reddit.com/r/bread/comments/def/bread/') ]}}
        session.get = Mock(return_value=response)
        report = search(config(), 'fake-key', True, session=session, sleep=Mock())
        self.assertEqual(len(report['candidates']), 1)
        self.assertEqual(report['requests_made'], 3)
        self.assertEqual(report['status'], 'insufficient_candidates')
        session.close()

    def test_rate_limit_stops_and_writes_honest_report(self):
        session = requests.Session()
        response = Mock(status_code=429, headers={'Retry-After': '60'})
        session.get = Mock(return_value=response)
        report = search(config(), 'fake-key', True, session=session)
        self.assertEqual(report['requests_made'], 1)
        self.assertEqual(report['status'], 'blocked')
        self.assertIn('Retry-After: 60', report['errors'][0])
        with tempfile.TemporaryDirectory() as directory:
            save(report, directory)
            self.assertTrue((Path(directory)/'discovery.json').exists())
        session.close()
