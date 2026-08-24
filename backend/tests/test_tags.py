"""Tag normalization — the pasted-hashtag-blob case that produced mega-tags."""
from services.tags import normalize_tag_csv


def test_plain_csv_unchanged():
    assert normalize_tag_csv("burlesque, stage, nola") == "burlesque, stage, nola"


def test_multiword_tags_still_collapse():
    # The original intent: platform hashtags can't contain spaces.
    assert normalize_tag_csv("New Orleans, Slow Burn") == "NewOrleans, SlowBurn"


def test_pasted_hashtag_block_explodes():
    raw = "2026, burlesque, @teaserfestival@darrellmillerphotography#Burlesque#NOLA#burlyq"
    out = normalize_tag_csv(raw)
    # "#Burlesque" dedupes against the earlier plain "burlesque" — case-insensitive,
    # first spelling wins.
    assert out == "2026, burlesque, teaserfestival, darrellmillerphotography, NOLA, burlyq"


def test_space_separated_hashtags_explode():
    out = normalize_tag_csv("#StagePhotography #NOLAArts #Cabaret")
    assert out == "StagePhotography, NOLAArts, Cabaret"


def test_dedupes_case_insensitively_across_exploded_tokens():
    out = normalize_tag_csv("nola, #NOLA, @nola")
    assert out == "nola"


def test_empty_and_symbol_only():
    assert normalize_tag_csv("") is None
    assert normalize_tag_csv("  ") is None
    assert normalize_tag_csv("#, @, #@") is None
