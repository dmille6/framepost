"""Names need evidence; vocabulary spelling belongs to the photographer."""
from types import SimpleNamespace

import pytest

from models import AppConfig, Post, TagProfile
from services import ai_tagging, alt_text, tags


@pytest.mark.parametrize("tone", ["concise", "descriptive"])
@pytest.mark.parametrize("description", [None, "A performer under blue light."])
@pytest.mark.parametrize("title", [None, "Jane at The Stage in Austin"])
def test_name_rule_in_every_prompt_mode(tone, description, title):
    prompt = ai_tagging.build_prompt(max_tags=10, hint_title=title, hint_tags=None,
                                    hint_description=description, tone=tone)
    assert ai_tagging.NAME_GROUNDING_RULE in prompt
    assert "person, band, venue, event, or place" in prompt
    assert "provided context/title or clearly legible in the image" in prompt
    assert "'a guitarist'" in prompt
    assert ai_tagging.NAME_GROUNDING_RULE in ai_tagging.PROMPT.format(max_tags=10)


def test_vocabulary_case_plural_new_tags_and_conservative_nonmatches(db):
    db.add(Post(id="vocab", tags="NOLA, Guitar, performers, bass, bus, city, Glass"))
    db.add(TagProfile(id="profile", name="Saved", tags="StageLight, Guitar"))
    db.commit()
    assert tags.snap_to_vocabulary(db, [
        "nola", "guitars", "Performer", "stagelights", "  New Phrase  ",
        "basses", "buses", "cities", "glasses", "guitarr",
    ]) == ["NOLA", "Guitar", "performers", "StageLight", "New Phrase",
           "basses", "buses", "cities", "glasses", "guitarr"]


def test_exact_saved_plural_wins_and_common_casing_is_stable(db):
    db.add_all([Post(id="a", tags="Guitar, guitars, NOLA"),
                Post(id="b", tags="Nola"), Post(id="c", tags="Nola")])
    db.commit()
    assert tags.snap_to_vocabulary(db, ["GUITARS", "guitar", "nola"]) == ["guitars", "Guitar", "Nola"]


def test_snapping_dedupes_without_losing_provider_sources(db):
    db.add(Post(id="vocab", tags="Guitar"))
    db.commit()
    result = ai_tagging.TagSuggestion(tags=["guitars", "GUITAR", "New"],
                                     sources=[["anthropic"], ["openai"], ["openai"]])
    ai_tagging.snap_suggestion(db, result)
    assert result.tags == ["Guitar", "New"]
    assert result.sources == [["anthropic", "openai"], ["openai"]]


@pytest.fixture()
def fake_suggester(monkeypatch):
    fake = SimpleNamespace(
        is_configured=lambda: True,
        suggest=lambda **kw: ai_tagging.TagSuggestion(tags=["guitars"], alt_text="A guitarist."),
    )
    monkeypatch.setattr(ai_tagging, "for_provider", lambda p: fake)
    return fake


def test_interactive_route_snaps_suggestions(db, monkeypatch, tmp_path, fake_suggester):
    from routes import ai
    src = tmp_path / "photo.jpg"
    src.touch()
    db.add(Post(id="photo", tags="Guitar", original_path=str(src)))
    db.commit()
    assert ai.suggest("photo", db=db, _user=None).tags == ["Guitar"]


def test_import_auto_apply_snaps_before_merging(db, monkeypatch, tmp_path, fake_suggester):
    import database
    src = tmp_path / "photo.jpg"
    src.touch()
    db.add_all([Post(id="old", tags="Guitar"), Post(id="new", original_path=str(src)),
                AppConfig(key="ai_tagging_enabled", value="true"),
                AppConfig(key="ai_auto_apply", value="true")])
    db.commit()
    monkeypatch.setattr(database, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    ai_tagging.apply_to_post("new")
    assert db.get(Post, "new").tags == "Guitar"


def test_alt_text_sweep_uses_guarded_prompt(db, monkeypatch, tmp_path, fake_suggester):
    src = tmp_path / "photo.jpg"
    src.touch()
    db.add_all([Post(id="photo", original_path=str(src)),
                AppConfig(key="ai_tagging_enabled", value="true")])
    db.commit()
    monkeypatch.setattr(alt_text.storage, "preview_path", lambda _: src)
    prompts = []

    def suggest(**kw):
        kw.pop("image_path")
        kw.pop("full_resolution")
        prompts.append(ai_tagging.build_prompt(**kw))
        return ai_tagging.TagSuggestion(tags=[], alt_text="A guitarist.")

    fake_suggester.suggest = suggest
    assert alt_text.fill_missing_alt_text(db) == 1
    assert ai_tagging.NAME_GROUNDING_RULE in prompts[0]
