"""Which groups a post belongs in, decided from the post instead of by hand.

Picking groups from a checklist scales badly: the roster is ~25 pools and 517 of
518 posts carry a stage/performance tag, so for most groups every post is a yes
and the clicking is pure ceremony.

Two rules replace it. `default_enabled` marks the groups that take any subject
the photographer shoots -- technique and gear pools, and the exact-subject ones.
`match_tags` gates the rest: a concert pool is a real fit for the ~14% of the
catalogue tagged concert/livemusic and a bad fit for the other 86%, and
submitting the 86% anyway is how a group moderator removes you.

Assignments are materialised once, when the photo actually lands on Flickr, so
the tags read here are final. A post that already has assignments is never
touched -- that's what makes the per-post override in routes/groups.py stick.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Group, Post, PostGroup
from services import tags as tags_svc


def _norm(tag: str) -> str:
    """Compare tags case- and space-insensitively.

    Stored tags keep the photographer's casing ('NouvelleFollies', 'a7rIII') and
    are already space-free, so folding case is all that's needed to make
    'Burlesque' in a rule match 'burlesque' on a post.
    """
    return tags_svc.normalize_tag(tag).lower()


def post_tags(post: Post) -> set[str]:
    return {_norm(t) for t in tags_svc.parse_csv(post.tags) if t.strip()}


def group_rule(group: Group) -> set[str]:
    """Tags that satisfy this group's condition; empty means unconditional."""
    return {_norm(t) for t in tags_svc.parse_csv(group.match_tags) if t.strip()}


def accepts(group: Group, tags: set[str]) -> bool:
    """True when this group should receive a post carrying `tags`."""
    if not group.default_enabled:
        return False
    rule = group_rule(group)
    if not rule:
        return True
    return bool(rule & tags)


def resolve(db: Session, post: Post) -> list[Group]:
    """Groups a post qualifies for, by the two rules above."""
    tags = post_tags(post)
    return [
        g for g in db.execute(select(Group)).scalars().all()
        if g.flickr_group_id and accepts(g, tags)
    ]


def ensure_assignments(db: Session, post: Post) -> list[Group]:
    """Materialise pending submissions for a freshly published post.

    No-op when the photographer already decided. That covers both an existing
    assignment and `groups_overridden`, which records the act of saving a
    selection -- without it, clearing every group off a post would read as
    "untouched" and get refilled with the defaults. Callers commit.
    """
    if post.groups_overridden:
        return []
    already = db.execute(
        select(PostGroup.id).where(PostGroup.post_id == post.id).limit(1)
    ).first()
    if already:
        return []

    chosen = resolve(db, post)
    for group in chosen:
        db.add(PostGroup(
            id=uuid.uuid4().hex,
            post_id=post.id,
            group_id=group.id,
            status="pending",
        ))
    return chosen
