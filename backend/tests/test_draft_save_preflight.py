"""Saving even an empty edit must preserve the server's Schedule blocker."""
import pytest
from models import Post
from routes.posts import PostUpdate, get_post, list_drafts, update_post


@pytest.mark.parametrize('changes', [{}, {'title': 'Changed'}])
def test_patch_and_detail_return_the_same_preflight_as_drafts(db, changes):
    db.add(Post(id='p', title='Original', status='pending', target_platforms='["flickr"]',
                original_path='/missing/original'))
    db.commit()
    expected = list_drafts(db=db, _user=None, limit=100, offset=0)[0].preflight
    assert expected['deliverable'] is False
    saved = update_post('p', PostUpdate(**changes), db=db, _user=None)
    assert saved.preflight == expected
    assert get_post('p', db=db, _user=None).preflight == expected
