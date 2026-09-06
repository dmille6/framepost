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
