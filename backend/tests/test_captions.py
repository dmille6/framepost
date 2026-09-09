import re
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


def test_generic_medium_words_lose_their_slot_to_specific_tags(db):
    """The Freakshow set was the tell: #dance and #fire took two of five slots ahead of
    #sideshow and #fireperformer purely because Lightroom hands keywords over
    alphabetised. Bare medium nouns are demoted to filler."""
    post = _post(title="Freakshow", description="Sideshow act.",
                 tags="dance, fire, performance, performer, stage, theater, "
                      "circussideshow, freakshowpeepshow, fireperformer, fireeating, "
                      "sideshow")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert caption.count("#") == 5
    for generic in ("#dance", "#fire ", "#performance", "#performer", "#stage",
                    "#theater"):
        assert generic not in caption, generic
    for specific in ("#circussideshow", "#freakshowpeepshow", "#fireperformer",
                     "#fireeating", "#sideshow"):
        assert specific in caption, specific


def test_generics_still_fill_the_block_when_nothing_specific_exists(db):
    """Demotion, not exclusion — a post tagged only in generics keeps all five slots."""
    post = _post(title="Stage", description="On stage.",
                 tags="dance, fire, performance, performer, stage, theater, costume")
    db.add(post)
    db.commit()
    assert _build_caption_for("instagram", post, db).count("#") == 5


def test_niche_subject_words_are_not_treated_as_generic(db):
    """burlesque/circus/drag/sideshow are single words but they ARE the niche — they
    must not be swept up with #dance and #stage."""
    post = _post(title="Bits", description="Variety night.",
                 tags="stage, performance, theater, costume, audience, spotlight, "
                      "burlesque, circus, drag, sideshow, cabaret")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    for subject in ("#burlesque", "#circus", "#drag", "#sideshow", "#cabaret"):
        assert subject in caption, subject
    assert caption.count("#") == 5


def test_export_pipeline_markers_never_take_a_slot(db):
    """"exported" and "postframe" are written by the export pipeline, not by a human
    describing the picture."""
    post = _post(title="Ari", description="Aerial silks.",
                 tags="exported, postframe, aerialsilks, aerialist, aerialperformer, "
                      "circusarts, nolacircus")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert "#exported" not in caption
    assert "#postframe" not in caption
    assert caption.count("#") == 5
    # Untouched in the long block, same as the gear tags.
    assert "#exported" in _build_caption_for("pixelfed", post, db)


def test_a_slot_is_reserved_for_a_subject_tag(db):
    """The High Society run came out as performer + venue + venue + show + city, with
    nothing saying what the picture shows. Proper nouns are maximally specific and often
    have no audience at all — nobody searches #allways."""
    post = _post(title="Miss Angie Z", description="High Society at the AllWays.",
                 tags="angiez, allways, allwayslounge, highsociety, neworleans, "
                      "burlesque, burlyq, cabaret")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert caption.count("#") == 5
    assert "#burlesque" in caption
    # Paid for out of the weakest of the five, not the strongest.
    assert "#angiez" in caption
    assert "#neworleans" not in caption


def test_the_reserve_is_a_no_op_when_a_subject_tag_already_made_it(db):
    """It holds one slot, it doesn't add a second."""
    post = _post(title="Freakshow", description="Sideshow act.",
                 tags="circus, circussideshow, freakshowpeepshow, nola, republicnola, "
                      "sideshow, fireperformer")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert caption.count("#") == 5
    assert "#circus " in caption and "#circussideshow" in caption
    assert "#republicnola" in caption      # venue kept its slot


def test_the_reserve_stays_out_of_the_way_with_no_subject_tag_to_promote(db):
    post = _post(title="Venue", description="Room shot.",
                 tags="allways, allwayslounge, highsociety, neworleans, frenchquarter, "
                      "joytheater")
    db.add(post)
    db.commit()
    assert _build_caption_for("instagram", post, db).count("#") == 5


def test_month_keywords_are_junk_like_years(db):
    """Lightroom writes a month alongside the year, and the No Ring Circus set was
    spending one of five slots on "#jan"."""
    post = _post(title="No Ring Circus", description="House of Blues.",
                 tags="jan, 2026, circus, circusarts, circusperformer, sideshow, "
                      "acrobatics, aerialist")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert "#jan" not in caption
    assert "#2026" not in caption
    assert caption.count("#") == 5


def test_near_duplicate_tags_dont_monopolise_the_five(db):
    """Worship Burlesque spent every slot on one word — five tags reaching one audience,
    with the venue, city and performer left untagged."""
    post = _post(title="Hellin Heels", description="Worship Burlesque.",
                 tags="burlesque, burlesquedancer, burlesquefest, burlesquelife, "
                      "burlesqueperformer, hiholoungenola, marigny, hellinheels")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    assert caption.count("#") == 5
    assert len([t for t in re.findall(r"#(\w+)", caption) if t.startswith("burles")]) == 2
    assert "#hiholoungenola" in caption          # venue got a slot back
    assert "#hellinheels" in caption             # so did the performer


def test_stem_spread_backfills_rather_than_starving_the_block(db):
    """A post tagged entirely in one family still gets five."""
    post = _post(title="Hellin Heels", description="Worship Burlesque.",
                 tags="burlesque, burlesquedancer, burlesquefest, burlesquelife, "
                      "burlesqueperformer, burlesqueshow, burlesqueart")
    db.add(post)
    db.commit()
    assert _build_caption_for("instagram", post, db).count("#") == 5


def test_stem_spread_leaves_the_long_block_alone(db):
    post = _post(title="Hellin Heels", description="Worship Burlesque.",
                 tags="burlesque, burlesquedancer, burlesquefest, burlesquelife, "
                      "burlesqueperformer, burlesqueshow, burlesqueart")
    db.add(post)
    db.commit()
    assert _build_caption_for("pixelfed", post, db).count("#") == 7


def test_scene_synonyms_count_as_the_same_stem(db):
    """#burlyq and #burlylife are #burlesque by another name, which no prefix can see."""
    post = _post(title="Hellin Heels", description="Worship Burlesque.",
                 tags="burlesque, burlesquedancer, burlylife, burlyq, cabaret, "
                      "stvicenthotel, livemusic")
    db.add(post)
    db.commit()
    caption = _build_caption_for("instagram", post, db)
    tags = re.findall(r"#(\w+)", caption)
    assert len(tags) == 5
    assert len([t for t in tags if t.startswith(("burles", "burly"))]) == 2
    assert "#stvicenthotel" in caption          # the venue got a slot


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
