"""The draft queue's unparameterised request must include the whole show."""
from models import Post


def test_default_draft_listing_is_not_capped(db):
    db.add_all([Post(id=f'p{i:04}', status='pending') for i in range(525)])
    db.add(Post(id='published', status='posted'))
    db.commit()
    # Direct route execution keeps the test's SQLite session on its creating thread.
    # Resolve Query defaults exactly as an unparameterised HTTP request would.
    from inspect import signature
    from routes.posts import list_drafts
    params = signature(list_drafts).parameters
    result = list_drafts(db=db, _user=None, limit=params['limit'].default.default,
                         offset=params['offset'].default.default)
    assert len(result) == 525
    assert {p.id for p in result} == {f'p{i:04}' for i in range(525)}
    page = list_drafts(db=db, _user=None, limit=7, offset=100)
    assert [p.id for p in page] == [p.id for p in result[100:107]]
