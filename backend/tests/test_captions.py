"""Caption-building regressions: title echo, hashtag legality."""
import uuid

from models import Post
from services import performers as performers_svc
from services.scheduler import _build_caption_for


def test_hashtag_safe_strips_periods():
    assert performers_svc._hashtag_safe("the.no.ring.circus") == "thenoringcircus"
    assert performers_svc._hashtag_safe("mx.eli.rose") == "mxelirose"
    assert performers_svc._hashtag_safe("plain_handle") == "plain_handle"
    assert performers_svc._hashtag_safe("...") == ""
    assert performers_svc._hashtag_safe(None) == ""


def test_hashtag_tokens_from_dotted_handles():
    from models import Performer
    performers = [
        Performer(id="1", display_name="The No Ring Circus", instagram_handle="the.no.ring.circus"),
        Performer(id="2", display_name="Mx Eli Rose", instagram_handle="mx.eli.rose"),
    ]
    tokens = performers_svc.hashtag_tokens(performers)
    assert tokens == ["#thenoringcircus", "#mxelirose"]


def _post(**kw) -> Post:
    return Post(id=uuid.uuid4().hex, status="pending", **kw)


def test_caption_drops_title_when_description_opens_with_it(db):
    post = _post(
        title="The No Ring Circus - House of Blues - New Orleans",
        description="The No Ring Circus - House of Blues - New Orleans\nJanuary 2026",
    )
    db.add(post)
    db.commit()
    caption = _build_caption_for("pixelfed", post, db)
    # The title line appears exactly once (leading the description), not stacked twice.
    assert caption.lower().count("the no ring circus - house of blues") == 1
    assert caption.startswith("The No Ring Circus")


def test_caption_keeps_title_when_description_differs(db):
    post = _post(title="Juju", description="Fire poi at the AllWays Lounge.")
    db.add(post)
    db.commit()
    caption = _build_caption_for("pixelfed", post, db)
    assert caption.startswith("Juju\n\nFire poi")


def test_bare_tag_does_not_suppress_the_mention(db):
    """Regression: a keyword tag matching a performer's handle used to make the caption
    builder treat the handle as already mentioned, dropping the @mention entirely."""
    import uuid
    from models import Performer, PostPerformer

    perf = Performer(id=uuid.uuid4().hex, display_name="Bebe", instagram_handle="bebe.bardeaux")
    post = _post(title="Bebe at Teaser Fest", description="Fire and feathers.",
                 tags="burlesque, bebe.bardeaux, nola")
    db.add_all([perf, post])
    db.flush()
    db.add(PostPerformer(post_id=post.id, performer_id=perf.id, position=0))
    db.commit()

    ctx = performers_svc.caption_context_for_post(db, post)
    assert ctx.mention_block == "@bebe.bardeaux"     # mention survives the tag
    assert ctx.hashtag_tokens == []                  # hashtag comes from the tag block


def test_handle_written_in_text_still_suppresses_the_mention(db):
    """The opposite case must keep working: don't double up when the user typed it."""
    import uuid
    from models import Performer, PostPerformer

    perf = Performer(id=uuid.uuid4().hex, display_name="Eddie", instagram_handle="onlyeddielockwood")
    post = _post(title="Eddie Lockwood", description="Eddie - @onlyeddielockwood - No Ring Circus")
    db.add_all([perf, post])
    db.flush()
    db.add(PostPerformer(post_id=post.id, performer_id=perf.id, position=0))
    db.commit()

    ctx = performers_svc.caption_context_for_post(db, post)
    assert ctx.mention_block == ""
