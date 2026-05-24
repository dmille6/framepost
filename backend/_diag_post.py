"""Diagnose what we'd actually send Flickr for the failing DandyDillinger post."""
from pathlib import Path

from sqlalchemy import select

from database import SessionLocal
from models import Post
from services import image, storage, tags as tag_helpers
from services.platforms import flickr as flickr_svc


def main() -> None:
    db = SessionLocal()
    try:
        p = db.execute(
            select(Post).where(Post.original_filename.like("%DandyDillinger%"))
        ).scalar_one_or_none()
        if not p:
            print("post not found")
            return
        src = Path(p.original_path)

        # 2048px derivative
        dst = storage.DERIVATIVES / f"_test_{p.id}.jpg"
        image.make_derivative(src, dst, 2048)
        size_2048 = dst.stat().st_size
        print(f"derivative @ 2048: {size_2048 / 1024 / 1024:.2f} MB")
        dst.unlink()

        # 1600px
        dst = storage.DERIVATIVES / f"_test_1600_{p.id}.jpg"
        image.make_derivative(src, dst, 1600)
        size_1600 = dst.stat().st_size
        print(f"derivative @ 1600: {size_1600 / 1024 / 1024:.2f} MB")
        dst.unlink()

        merged = tag_helpers.merged_tags_for_post(db, p)
        machine_tag = f"framepost:sha256={p.sha256}"
        flickr_formatted = flickr_svc.format_tags(merged, machine_tags=[machine_tag])

        comma_count = len(merged.split(","))
        print()
        print(f"user tags on post: {p.tags!r}")
        print(f"merged tags ({comma_count} tags, {len(merged)} chars):")
        print(f"  {merged!r}")
        print()
        print(f"final flickr-formatted tags ({len(flickr_formatted)} chars):")
        print(f"  {flickr_formatted!r}")
        print()
        print(f"title ({len(p.title or '')} chars): {p.title!r}")
        if p.description:
            print(f"description ({len(p.description)} chars): {p.description[:200]}{'...' if len(p.description) > 200 else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
