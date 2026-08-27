"""`adapters`: the shared Atom core plus the per-app parsers that turn
raw Atom/JSON bytes from the (real or fake) Wikis/Blogs/Forums APIs
into typed, normalized entities.

Pure functions over bytes -- no network, no archive writes. See
`connections_export/adapters/wikis.py`, `blogs.py`, and `forums.py` for the
parsers themselves; `connections_export/adapters/model.py` for the entities
they return; and `connections_export/adapters/profiles.py` for the per-app
transport capability data (`BLOGS`/`FORUMS`).
"""

from connections_export.adapters.blogs import (
    blogs_list_url,
    entries_feed_url,
    entry_comments_url,
    parse_blogs_feed,
    parse_entries_feed,
    parse_entry_comments_feed,
    service_doc_url,
    single_entry_subscription_url,
)
from connections_export.adapters.errors import AdapterError
from connections_export.adapters.forums import (
    build_reply_tree,
    entity_type,
    forums_list_url,
    parse_replies_feed,
    parse_topics_feed,
    replies_url,
    reply_url,
    topic_url,
    topics_url,
)
from connections_export.adapters.model import (
    Attachment,
    BlogComment,
    BlogPost,
    BlogRef,
    Comment,
    ForumReply,
    ForumTopic,
    NavNode,
    NavTree,
    Page,
    ReplyNode,
    Tag,
    Version,
    WikiRef,
)
from connections_export.adapters.profiles import BLOGS, FORUMS, AppProfile
from connections_export.adapters.wikis import (
    artifacts_url,
    nav_feed_url,
    page_entry_url,
    parse_attachments_feed,
    parse_comments_feed,
    parse_nav_feed,
    parse_navigation_entry,
    parse_page_entry,
    parse_tags_feed,
    parse_versions_feed,
    parse_wikis_feed,
    wikis_feed_url,
)

__all__ = [
    "AdapterError",
    "AppProfile",
    "Attachment",
    "BLOGS",
    "BlogComment",
    "BlogPost",
    "BlogRef",
    "Comment",
    "FORUMS",
    "ForumReply",
    "ForumTopic",
    "NavNode",
    "NavTree",
    "Page",
    "ReplyNode",
    "Tag",
    "Version",
    "WikiRef",
    "artifacts_url",
    "blogs_list_url",
    "build_reply_tree",
    "entity_type",
    "entries_feed_url",
    "entry_comments_url",
    "forums_list_url",
    "nav_feed_url",
    "page_entry_url",
    "parse_attachments_feed",
    "parse_blogs_feed",
    "parse_comments_feed",
    "parse_entries_feed",
    "parse_entry_comments_feed",
    "parse_nav_feed",
    "parse_navigation_entry",
    "parse_page_entry",
    "parse_replies_feed",
    "parse_tags_feed",
    "parse_topics_feed",
    "parse_versions_feed",
    "parse_wikis_feed",
    "replies_url",
    "reply_url",
    "service_doc_url",
    "single_entry_subscription_url",
    "topic_url",
    "topics_url",
    "wikis_feed_url",
]
