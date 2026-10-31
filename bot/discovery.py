from typing import Protocol

from .access import AccessStopped, ORIGIN
from .models import Config, DiscoveryResult
from .parsing import SemanticHTMLParser


class DiscoveryAdapter(Protocol):
    """An approved API adapter can implement this without changing selection."""
    def discover(self, config: Config) -> DiscoveryResult: ...


class PublicPageAdapter:
    def __init__(self, client, parser=None):
        self.client = client
        self.parser = parser or SemanticHTMLParser()

    def discover(self, config):
        result = DiscoveryResult(source_status='blocked')
        listings = ([f'{ORIGIN}/r/{sub}/new/' for sub in config.subreddits]
                    if config.subreddits else [ORIGIN + '/new/'])
        try:
            robots = self.client.load_robots()
            for url in listings:
                if not robots.allowed(url):
                    self.client.stop('robots_disallowed', url,
                        'The directly retrieved robots.txt disallows this listing. No discussion pages were requested.')
            if not config.access.permission_reference.strip():
                raise AccessStopped('permission_unverified', ORIGIN,
                    'No applicable permission reference was supplied. Technical accessibility and robots Allow are not permission.')
            if not self.parser.verified_live_structure:
                raise AccessStopped('parser_unverified', ORIGIN,
                    'Actual public HTML could not be inspected under permitted access. The semantic parser is fixture-tested only; no production selectors are verified.')
            seen = set()
            for listing in listings:
                html = self.client.get(listing)
                urls = self.parser.links(html)
                if not urls:
                    result.errors.append(dict(code='structure_unknown', url=listing,
                                              detail='No supported discussion links; dynamic rendering or changed HTML is possible. No browser bypass attempted.'))
                for url in urls:
                    if url in seen:
                        continue
                    seen.add(url)
                    discussion = self.parser.discussion(self.client.get(url), url)
                    if discussion:
                        result.discussions.append(discussion)
                    else:
                        result.errors.append(dict(code='content_unknown', url=url,
                            detail='Supported post text/date/status structure was not available. No content was inferred.'))
                    if len(result.discussions) >= config.max_results:
                        result.source_status = 'completed'
                        return result
            result.source_status = 'completed_with_errors' if result.errors else 'completed'
        except AccessStopped as exc:
            result.errors.append(exc.record())
            result.source_status = 'partial_blocked' if result.discussions else 'blocked'
        return result
