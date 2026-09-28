"""Truthful delivery labels, including history written before the archive guard.

There is no 'partial' status in the existing queue vocabulary. A failed Flickr
archive remains failed while its individual platform rows show successful sends.
"""
from models import Post
from services import preflight


def status_for(post: Post) -> str:
    if (post.status in ("posted", "late") and not post.flickr_photo_id
            and "flickr" in preflight.targets_for(post, [])):
        return "failed"
    return post.status
