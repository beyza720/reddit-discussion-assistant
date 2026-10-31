"""GET-only public HTTP client. Never follows redirects or sends cookies."""
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import requests

AGENT = 'EducationalDiscussionReview/2.0'
ORIGIN = 'https://www.reddit.com'


class AccessStopped(RuntimeError):
    def __init__(self, code, url, detail):
        self.code, self.url, self.detail = code, url, detail
        super().__init__(detail)

    def record(self):
        return dict(code=self.code, url=self.url, detail=self.detail)


class Robots:
    """Conservative longest-match robots rules with * and $ support."""
    def __init__(self, text):
        groups, agents, rules = [], [], []
        for line in text.splitlines() + ['User-agent: __end__']:
            line = line.split('#', 1)[0].strip()
            if ':' not in line:
                continue
            name, value = (p.strip() for p in line.split(':', 1))
            name = name.lower()
            if name == 'user-agent':
                if rules:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif name in ('allow', 'disallow', 'crawl-delay') and agents:
                rules.append((name, value))
        matches = [(max((len(a) if a != '*' else 0 for a in names
                         if a == '*' or a in AGENT.lower()), default=-1), rs) for names, rs in groups]
        specificity = max((n for n, _ in matches), default=-1)
        if specificity < 0:
            raise ValueError('No applicable robots rules; refusing to assume permission')
        self.rules = [rule for n, rs in matches if n == specificity for rule in rs]
        self.delay = max((float(v) for k, v in self.rules if k == 'crawl-delay'), default=0)

    def allowed(self, url):
        path = urlsplit(url).path or '/'
        if urlsplit(url).query:
            path += '?' + urlsplit(url).query
        matches = []
        for name, pattern in self.rules:
            if name not in ('allow', 'disallow') or not pattern:
                continue
            end = pattern.endswith('$')
            expression = re.escape(pattern[:-1] if end else pattern).replace(r'\*', '.*')
            if re.match('^' + expression + ('$' if end else ''), path):
                matches.append((len(pattern.replace('*', '').rstrip('$')), name == 'allow'))
        return max(matches, default=(0, True))[1]


def challenged(html):
    lowered = html.lower()
    return any(marker in lowered for marker in (
        'g-recaptcha', 'h-captcha', 'cf-chl-', 'verify you are human',
        "you've been blocked by network security", 'whoa there, pardner',
        'your request has been blocked', 'challenge-platform'))


class PublicClient:
    def __init__(self, limits, state, session=None, sleep=time.sleep, clock=time.time):
        self.limits, self.state = limits, state
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.clear()
        self.session.headers.update({'User-Agent': AGENT, 'Accept': 'text/html,text/plain'})
        self.sleep, self.clock = sleep, clock
        self.count, self.last, self.stopped = 0, None, False
        self.cache = {}
        self.robots = None

    def stop(self, code, url, detail):
        self.stopped = True
        raise AccessStopped(code, url, detail)

    def get(self, url, robots=False):
        if self.stopped:
            raise AccessStopped('source_stopped', url, 'No further requests are permitted in this run.')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != 'www.reddit.com' or
                parsed.username or parsed.password or parsed.fragment):
            self.stop('unsupported_url', url, 'Only the configured public Reddit origin is supported.')
        if parsed.path != '/robots.txt' and robots:
            self.stop('invalid_robots_url', url, 'The robots exception only applies to /robots.txt.')
        if not robots and (self.robots is None or not self.robots.allowed(url)):
            self.stop('robots_disallowed', url, 'robots.txt disallows this URL or could not be verified.')
        not_before = float(self.state.get('reddit_not_before') or 0)
        if self.clock() < not_before:
            self.stop('retry_after_active', url, f'Retry-After prohibits requests until {datetime.fromtimestamp(not_before, timezone.utc).isoformat()}.')
        if url in self.cache:
            saved, body = self.cache[url]
            if self.clock() - saved < self.limits.cache_ttl_seconds:
                return body
        for attempt in range(self.limits.max_retries + 1):
            if self.count >= self.limits.max_requests:
                self.stop('request_limit', url, 'The total request limit, including retries and robots.txt, was reached.')
            delay = max(self.limits.min_interval_seconds, self.robots.delay if self.robots else 0)
            if delay > 60:
                self.stop('crawl_delay', url, f'The robots crawl delay is {delay}s; this short CLI run will not proceed.')
            if self.last is not None:
                self.sleep(max(0, delay - (self.clock() - self.last)))
            self.session.cookies.clear()
            self.count += 1
            self.last = self.clock()
            try:
                response = self.session.get(url, timeout=self.limits.timeout_seconds, allow_redirects=False, stream=True)
            except requests.RequestException as exc:
                if attempt == self.limits.max_retries:
                    self.stop('network_error', url, f'GET failed: {type(exc).__name__}. No content was collected.')
                continue
            try:
                if response.status_code == 429 or response.headers.get('Retry-After'):
                    raw = response.headers.get('Retry-After', '60')
                    try:
                        seconds = max(0, float(raw))
                    except ValueError:
                        try:
                            seconds = max(0, parsedate_to_datetime(raw).timestamp() - self.clock())
                        except (TypeError, ValueError, OverflowError):
                            seconds = 60
                    until = self.clock() + seconds
                    self.state.set('reddit_not_before', until)
                    self.stop('rate_limited', url, f'HTTP {response.status_code}; Retry-After={raw}. Source stopped; deadline persisted, not shortened.')
                if response.status_code in (401, 403, 451):
                    self.stop('access_denied', url, f'HTTP {response.status_code}. No login or bypass will be attempted.')
                if 300 <= response.status_code < 400:
                    self.stop('redirect', url, f'HTTP {response.status_code}. Redirects are not followed.')
                if response.status_code >= 500 and attempt < self.limits.max_retries:
                    continue
                if response.status_code != 200:
                    self.stop('http_error', url, f'HTTP {response.status_code}.')
                if not any(t in response.headers.get('Content-Type', '').lower() for t in ('text/html', 'text/plain')):
                    self.stop('content_type', url, 'Expected public HTML or robots text, not an API/private endpoint.')
                chunks, size = [], 0
                for chunk in response.iter_content(16384):
                    size += len(chunk)
                    if size > 2_000_000:
                        self.stop('response_size', url, 'Response exceeds the 2 MB collection limit.')
                    chunks.append(chunk)
                body = b''.join(chunks).decode('utf-8', errors='replace')
                if challenged(body):
                    self.stop('challenge', url, 'A CAPTCHA or access challenge was detected; source stopped.')
                self.cache[url] = (self.clock(), body)
                return body
            finally:
                response.close()
        self.stop('retry_limit', url, 'Bounded retries were exhausted.')

    def load_robots(self):
        text = self.get(ORIGIN + '/robots.txt', robots=True)
        try:
            self.robots = Robots(text)
        except (ValueError, OverflowError):
            self.stop('robots_unknown', ORIGIN + '/robots.txt', 'Could not safely parse applicable robots rules.')
        return self.robots

    def close(self):
        self.session.close()
