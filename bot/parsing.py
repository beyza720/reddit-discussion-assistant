"""Provisional semantic HTML parser, validated against synthetic fixtures only.

Reddit's actual raw HTML could not be inspected under the current access rules.
No production DOM selectors are asserted here. Live discovery remains disabled
until an authorized page-structure inspection verifies a parser implementation.
"""
import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .models import Discussion


def canonical_url(value):
    parsed = urlsplit(urljoin('https://www.reddit.com', value))
    if parsed.scheme != 'https' or parsed.netloc != 'www.reddit.com':
        return None
    match = re.fullmatch(r'/r/([A-Za-z0-9_]+)/comments/([A-Za-z0-9]+)(?:/[^/]+)?/?', parsed.path)
    if not match:
        return None
    return f'https://www.reddit.com/r/{match[1].lower()}/comments/{match[2].lower()}/'


def parse_date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def nodes(value):
    if isinstance(value, list):
        for item in value:
            yield from nodes(item)
    elif isinstance(value, dict):
        yield value
        if '@graph' in value:
            yield from nodes(value['@graph'])


class SemanticHTMLParser:
    verified_live_structure = False

    def links(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        return list(dict.fromkeys(url for a in soup.find_all('a', href=True)
                                  if (url := canonical_url(a['href']))))

    def discussion(self, html, source_url):
        soup = BeautifulSoup(html, 'html.parser')
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string or script.get_text())
            except (ValueError, TypeError):
                continue
            for item in nodes(data):
                kind = item.get('@type', [])
                if isinstance(kind, str):
                    kind = [kind]
                if not isinstance(kind, list) or 'DiscussionForumPosting' not in kind:
                    continue
                url = canonical_url(item.get('url', ''))
                if not url or url != canonical_url(source_url):
                    continue
                title = item.get('headline')
                text = item.get('text')
                if not isinstance(title, str) or not title.strip() or not isinstance(text, str):
                    continue
                properties = item.get('additionalProperty')
                properties = properties if isinstance(properties, list) else []
                props = {p.get('name'): p.get('value') for p in properties
                         if isinstance(p, dict) and isinstance(p.get('name'), str)}
                locked = props.get('locked') if type(props.get('locked')) is bool else None
                archived = props.get('archived') if type(props.get('archived')) is bool else None
                date = parse_date(item.get('datePublished'))
                prefix = source_url + '#json-ld:DiscussionForumPosting.'
                return Discussion(title=title[:500], url=url, subreddit=urlsplit(url).path.split('/')[2],
                    published_at=date, post_text=text[:6000], locked=locked, archived=archived,
                    sources={'title': prefix + 'headline', 'url': prefix + 'url (normalized)',
                             'subreddit': prefix + 'url path', 'published_at': prefix + 'datePublished' if date else 'unknown',
                             'post_text': prefix + 'text', 'locked': prefix + 'additionalProperty.locked' if locked is not None else 'unknown',
                             'archived': prefix + 'additionalProperty.archived' if archived is not None else 'unknown',
                             'comments': 'not collected', 'collected_at': 'local UTC clock'})
        return None
