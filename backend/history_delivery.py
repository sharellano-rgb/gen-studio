"""Compress history text without touching media or byte-range responses."""
from starlette.middleware.gzip import GZipMiddleware
import re


class HistoryCompression:
    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        listing = path in {'/api/images/history', '/api/jobs', '/api/scene-history', '/api/sonny-videos', '/api/sonny-images', '/api/assets'}
        listing = listing or bool(re.fullmatch(r'/api/admin/workspaces/[a-z][a-z0-9_-]{0,39}/(?:videos|images)', path))
        static = path.startswith('/assets/') and path.endswith(('.js', '.css'))
        ranged = any(key.lower() == b'range' for key, _ in scope.get('headers', []))
        target = self.compressed if scope['type'] == 'http' and (listing or static) and not ranged else self.app
        await target(scope, receive, send)
