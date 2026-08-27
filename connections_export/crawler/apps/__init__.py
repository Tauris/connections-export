"""One module per application the crawler traverses.

`crawl.py` held all five (then six) in 1,540 lines, against an `adapters/`
norm of one module per app. That size was not only a readability cost: it was
the main source of the repo's function-local imports, because a module that
large cannot be imported from cleanly.

Each module here owns one app's traversal and nothing else. What they share --
the run's bound paginator and the adapter-version stamps -- is in `_shared`,
deliberately small.
"""

from connections_export.crawler.apps.blogs import crawl_blogs
from connections_export.crawler.apps.community import crawl_community_logo
from connections_export.crawler.apps.files import crawl_files
from connections_export.crawler.apps.forums import crawl_forums
from connections_export.crawler.apps.rich_content import crawl_rich_content
from connections_export.crawler.apps.wikis import crawl

__all__ = [
    "crawl",
    "crawl_blogs",
    "crawl_community_logo",
    "crawl_files",
    "crawl_forums",
    "crawl_rich_content",
]
