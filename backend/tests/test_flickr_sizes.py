"""Which Flickr rendition we hand Meta."""
import xml.etree.ElementTree as ET

from services.platforms import flickr


def _sizes_xml(rows):
    root = ET.Element("rsp")
    sizes = ET.SubElement(root, "sizes")
    for label, w, h in rows:
        ET.SubElement(sizes, "size", label=label, width=str(w), height=str(h),
                      source=f"https://live.staticflickr.com/x/{label.replace(' ', '_')}.jpg")
    return root


def test_skips_a_rendition_that_is_really_the_original(monkeypatch):
    """The 2026-09-09 incident: a 1536x2048 upload makes Flickr serve "Large 2048" at the
    original's exact pixels, and Meta rejects it the same way it rejects `_o`. "Large 1600"
    of the same photo published fine."""
    monkeypatch.setattr(flickr, "rest_call",
                        lambda *a, **k: _sizes_xml([
                            ("Large 1600", 1200, 1600),
                            ("Large 2048", 1536, 2048),
                            ("Large", 768, 1024),
                            ("Original", 1536, 2048),
                        ]))
    url = flickr.get_display_image_url(None, "123")
    assert "Large_1600" in url, url


def test_uses_large_2048_when_it_is_a_genuine_downscale(monkeypatch):
    """A big upload makes "Large 2048" a real derivative, so the ladder should still
    prefer it over smaller rungs when asked for it."""
    monkeypatch.setattr(flickr, "rest_call",
                        lambda *a, **k: _sizes_xml([
                            ("Large 2048", 2048, 2731),
                            ("Large", 768, 1024),
                            ("Original", 4000, 5333),
                        ]))
    url = flickr.get_display_image_url(None, "123", preference=("Large 2048", "Large"))
    assert "Large_2048" in url, url


def test_falls_through_when_every_rung_matches_the_original(monkeypatch):
    """Degenerate case — a tiny upload where each rung is the original. Rather than raise,
    we still hand back a URL and let Meta judge it."""
    monkeypatch.setattr(flickr, "rest_call",
                        lambda *a, **k: _sizes_xml([
                            ("Large", 600, 800),
                            ("Original", 600, 800),
                        ]))
    url = flickr.get_display_image_url(None, "123")
    assert url.startswith("https://live.staticflickr.com/")
