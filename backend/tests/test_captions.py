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


def test_caption_drops_title_when_the_description_paraphrases_it(db):
    """The live failure. The AI tagger reworded the title into the description's opening
    sentence, so the old exact-prefix test passed it through and the Instagram caption
    went out saying the same thing twice."""
    post = _post(
        title='Hellin Heels - @_hellinheels_ - performing at "Teaser Fest" at Hotel Peter Paul New Orleans / Jan 2026',
        description='Hellin Heels - @_hellinheels_ - on stage during "Teaser Fest" at Hotel Peter Paul, New Orleans. Jan 2026.',
    )
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert caption.count("Hellin Heels") == 1
    assert caption.startswith("Hellin Heels - @_hellinheels_ - on stage during")


def test_caption_appends_shot_info_only_when_the_post_opts_in(db):
    fields = dict(camera_make="SONY", camera_model="ILCE-1", lens="FE 24-70mm F2.8 GM",
                  focal_length=70.0, aperture=2.8, shutter_speed="1/250", iso=3200)
    off = _post(title="Juju", description="Fire poi at the AllWays Lounge.", tags="fire", **fields)
    db.add(off)
    db.commit()
    assert "Sony" not in _build_caption_for("pixelfed", off, db)

    on = _post(title="Juju", description="Fire poi at the AllWays Lounge.", tags="fire",
               include_exif=1, **fields)
    db.add(on)
    db.commit()
    caption = _build_caption_for("pixelfed", on, db)
    shot = "Sony \u03b11 \u00b7 FE 24-70mm F2.8 GM \u00b7 70mm \u00b7 f/2.8 \u00b7 1/250s \u00b7 ISO 3200"
    assert shot in caption
    # Ahead of the hashtag block, behind the description — that's what "before the tags" means.
    assert caption.index("Fire poi") < caption.index(shot) < caption.index("#fire")


def test_bluesky_spends_its_budget_on_hashtags_not_camera_info(db):
    """Bluesky fits hashtags greedily into 300 chars. The shot line costs ~65 of them,
    which measured out to 4-6 hashtags dropped from every post in the queue."""
    post = _post(
        title="Miss Angie Z - High Society Burlesque - June 2026",
        description="Miss Angie Z on stage at the Allways Lounge, New Orleans. June 2026.",
        tags=" ".join(f"tag{n}" for n in range(20)),
        include_exif=1,
        camera_make="SONY", camera_model="ILCE-7RM3", lens="FE 24mm F1.4 GM",
        focal_length=24.0, aperture=2.8, shutter_speed="1/60", iso=3200,
    )
    db.add(post)
    db.commit()

    bluesky = _build_caption_for("bluesky", post, db)
    assert "Sony" not in bluesky
    assert len(bluesky) <= 300

    # Same post on Instagram, which has no budget pressure, keeps the line.
    assert "Sony \u03b17R III" in _build_caption_for("instagram", post, db)


def test_instagram_takes_five_hashtags_and_the_rest_keep_thirty(db):
    """Instagram capped posts at 5 hashtags in Dec 2025; Pixelfed made no such change."""
    post = _post(title="Juju", description="Fire poi at the AllWays Lounge.",
                 tags=" ".join(f"tag{n}" for n in range(20)))
    db.add(post)
    db.commit()
    assert _build_caption_for("instagram", post, db).count("#") == 5
    assert _build_caption_for("pixelfed", post, db).count("#") == 20


def test_the_five_slots_skip_gear_and_year_tags(db):
    """Lightroom keywords arrive alphabetised, so a naive cap took "2022", "a7r4" and
    "a7riv" before any subject tag — three of five slots on things nobody searches."""
    post = _post(title="Hellin Heels",
                 description="Burlesque performer on stage.",
                 tags="2022, a7r4, a7riv, sony, sonyalpha, darrellmillerphotography, "
                      "burlesque, showgirl, nolaburlesque, cabaret, burlesqueperformer")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    for junk in ("#2022", "#a7r4", "#a7riv", "#sony", "#sonyalpha",
                 "#darrellmillerphotography"):
        assert junk not in caption, junk
    assert "#burlesque" in caption
    assert caption.count("#") == 5
    # Pixelfed keeps them — the filter only applies where a cap is in force.
    assert "#a7r4" in _build_caption_for("pixelfed", post, db)


def test_subject_tags_outrank_performer_handles_when_capped(db):
    """A three-collaborator post would otherwise spend every slot on handles, which the
    @mention and the collaborator invite already cover."""
    import uuid
    from models import Performer, PostPerformer

    post = _post(title="Freakshow", description="Sideshow act on stage.",
                 tags="circussideshow, sideshowperformer, circusarts, "
                      "neworleanscircus, circusphotography")
    db.add(post)
    db.flush()
    for i, h in enumerate(("misstigerlily_", "republicnola", "freakshownola")):
        perf = Performer(id=uuid.uuid4().hex, display_name=h, instagram_handle=h)
        db.add(perf)
        db.flush()
        db.add(PostPerformer(post_id=post.id, performer_id=perf.id, position=i))
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert caption.count("#") == 5
    assert "#circussideshow" in caption
    assert "#republicnola" not in caption      # handle didn't displace a subject tag
    assert "@republicnola" in caption          # but the credit is still there


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
