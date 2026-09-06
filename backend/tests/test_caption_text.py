"""Rules shared by every caption builder: title-redundancy and the shot-info line."""
import uuid

from models import Post
from services import caption_text


def _post(**kw) -> Post:
    return Post(id=uuid.uuid4().hex, status="pending", **kw)


# --- title redundancy ----------------------------------------------------------------

def test_paraphrased_title_is_redundant():
    """The live failure: AI wrote the description as a reworded title, so the exact-prefix
    test passed it through and the caption said the same thing twice."""
    assert caption_text.title_is_redundant(
        'Hellin Heels - @_hellinheels_ - performing at "Teaser Fest" at Hotel Peter Paul '
        "New Orleans / Jan 2026",
        'Hellin Heels - @_hellinheels_ - on stage during "Teaser Fest" at Hotel Peter '
        "Paul, New Orleans. Jan 2026.",
    )


def test_verbatim_title_is_redundant():
    assert caption_text.title_is_redundant(
        "The No Ring Circus - House of Blues - New Orleans",
        "The No Ring Circus - House of Blues - New Orleans\nJanuary 2026",
    )


def test_title_that_adds_information_is_kept():
    assert not caption_text.title_is_redundant("Juju", "Fire poi at the AllWays Lounge.")
    assert not caption_text.title_is_redundant(
        "Some performances don't stay on the stage.",
        "@beatsantique reaching straight into the crowd.",
    )


def test_shared_stopwords_alone_do_not_make_a_title_redundant():
    assert not caption_text.title_is_redundant("Tiger Lilly", "A performer on the stage")


def test_empty_title_is_not_redundant():
    assert not caption_text.title_is_redundant("", "anything")
    assert not caption_text.title_is_redundant(None, None)


# --- shot info -----------------------------------------------------------------------

def test_shot_info_translates_sony_model_codes():
    post = _post(camera_make="SONY", camera_model="ILCE-7RM3", lens="FE 24-70mm F2.8 GM",
                 focal_length=38.0, aperture=2.8, shutter_speed="1/320", iso=3200)
    assert caption_text.format_shot_info(post) == (
        "Sony α7R III · FE 24-70mm F2.8 GM · 38mm · f/2.8 · 1/320s · ISO 3200"
    )


def test_shot_info_handles_plain_and_marked_bodies():
    assert caption_text.format_shot_info(_post(camera_make="SONY", camera_model="ILCE-1")) == "Sony α1"
    assert caption_text.format_shot_info(_post(camera_make="SONY", camera_model="ILCE-1M2")) == "Sony α1 II"
    assert caption_text.format_shot_info(_post(camera_make="SONY", camera_model="ILCE-7CR")) == "Sony α7CR"
    assert caption_text.format_shot_info(_post(camera_make="Canon", camera_model="EOS R5")) == "Canon EOS R5"


def test_shot_info_does_not_repeat_a_make_the_model_already_carries():
    post = _post(camera_make="LEICA CAMERA AG", camera_model="LEICA Q3 43")
    assert caption_text.format_shot_info(post) == "LEICA Q3 43"


def test_shot_info_trims_trailing_zeros():
    post = _post(camera_make="SONY", camera_model="ILCE-1", focal_length=150.0, aperture=4.0)
    assert caption_text.format_shot_info(post) == "Sony α1 · 150mm · f/4"


def test_shot_info_skips_what_it_does_not_know():
    assert caption_text.format_shot_info(_post()) == ""
    assert caption_text.format_shot_info(_post(iso=800)) == "ISO 800"


def test_description_only_grows_when_the_post_opts_in():
    fields = dict(camera_make="SONY", camera_model="ILCE-1", iso=1600)
    off = _post(description="On stage at Teaser Fest.", **fields)
    assert caption_text.description_with_shot_info(off) == "On stage at Teaser Fest."

    on = _post(description="On stage at Teaser Fest.", include_exif=1, **fields)
    assert caption_text.description_with_shot_info(on) == (
        "On stage at Teaser Fest.\n\nSony α1 · ISO 1600"
    )


def test_shot_info_stands_alone_when_there_is_no_description():
    post = _post(description=None, include_exif=1, camera_make="Canon", camera_model="EOS R5")
    assert caption_text.description_with_shot_info(post) == "Canon EOS R5"


def test_opting_in_with_no_exif_leaves_the_description_alone():
    post = _post(description="On stage.", include_exif=1)
    assert caption_text.description_with_shot_info(post) == "On stage."


def test_camera_name_is_the_name_a_photographer_would_write():
    """Shown in the editor's EXIF table as well as the caption, so the same body can't be
    named two different ways an inch apart on screen."""
    assert caption_text.format_camera_name(
        _post(camera_make="SONY", camera_model="ILCE-7RM4")) == "Sony \u03b17R IV"
    assert caption_text.format_camera_name(
        _post(camera_make="Canon", camera_model="EOS R5")) == "Canon EOS R5"
    assert caption_text.format_camera_name(
        _post(camera_make="LEICA CAMERA AG", camera_model="LEICA Q3 43")) == "LEICA Q3 43"


def test_camera_name_is_empty_when_unknown():
    assert caption_text.format_camera_name(_post()) == ""


def test_camera_name_matches_the_body_the_shot_line_uses():
    post = _post(camera_make="SONY", camera_model="ILCE-7RM3", iso=800)
    assert caption_text.format_shot_info(post).startswith(
        caption_text.format_camera_name(post))


# --- which platforms carry the line --------------------------------------------------

def _shot_post():
    return _post(description="On stage.", include_exif=1,
                 camera_make="SONY", camera_model="ILCE-1")


def test_bluesky_and_flickr_are_left_out():
    """Bluesky's 300-char budget is worth more as hashtags, and Flickr already shows
    EXIF in its own panel."""
    post = _shot_post()
    assert caption_text.description_for("bluesky", post) == "On stage."
    assert caption_text.description_for("flickr", post) == "On stage."


def test_platforms_that_show_no_exif_get_the_line():
    post = _shot_post()
    for platform in ("instagram", "pixelfed", "mastodon", "pinterest"):
        assert caption_text.description_for(platform, post) == "On stage.\n\nSony \u03b11", platform


def test_an_unknown_platform_gets_the_line():
    """New platforms opt out by name for a stated reason, rather than being forgotten."""
    assert caption_text.description_for("threads", _shot_post()).endswith("Sony \u03b11")


def test_opting_the_post_out_still_wins_everywhere():
    post = _post(description="On stage.", camera_make="SONY", camera_model="ILCE-1")
    for platform in ("instagram", "bluesky", "flickr", "pinterest"):
        assert caption_text.description_for(platform, post) == "On stage.", platform


# --- the "on by default" setting -----------------------------------------------------

def test_camera_info_is_on_for_new_posts_when_unset(db):
    from services.import_pipeline import _include_exif_default

    assert _include_exif_default(db) == 1


def test_camera_info_default_can_be_switched_off(db):
    from models import AppConfig
    from services.import_pipeline import _include_exif_default

    db.add(AppConfig(key="default_include_exif", value="false"))
    db.commit()
    assert _include_exif_default(db) == 0
