"""One-shot helper: run OpenAI and Anthropic taggers across recent imports for comparison.

Run with:  docker compose exec backend python _compare_providers.py
"""
from pathlib import Path

from sqlalchemy import select

from database import SessionLocal
from models import Post
from services.ai_tagging import AnthropicSuggester, OpenAISuggester


def main() -> None:
    db = SessionLocal()
    try:
        all_recent = (
            db.execute(
                select(Post)
                .where(Post.status == "pending")
                .order_by(Post.created_at.desc())
                .limit(15)
            )
            .scalars()
            .all()
        )
        targets = [
            p for p in all_recent if not (p.original_filename or "").startswith("test")
        ][:7]

        oa = OpenAISuggester()
        an = AnthropicSuggester()
        print(f"Running suggesters on {len(targets)} images.\n")

        for i, post in enumerate(targets, 1):
            fname = (post.original_filename or "")[:65]
            print("=" * 72)
            print(f"#{i}  {fname}")
            print(f"     {post.width}x{post.height}  ({post.id[:8]})")
            print()

            src = Path(post.original_path or "")
            if not src.exists():
                print("  source missing on disk")
                continue

            print("  OpenAI (gpt-4o-mini):")
            try:
                r = oa.suggest(image_path=src, max_tags=10, full_resolution=False)
                print(f"    tags ({len(r.tags)}): " + ", ".join(r.tags))
                print(f"    desc: {r.description}")
            except Exception as e:
                print(f"    ERROR: {e}")

            print()
            print("  Anthropic (claude-haiku-4-5):")
            try:
                r = an.suggest(image_path=src, max_tags=10, full_resolution=False)
                print(f"    tags ({len(r.tags)}): " + ", ".join(r.tags))
                print(f"    desc: {r.description}")
            except Exception as e:
                msg = str(e)
                tag = "SKIP (credits)" if "credit balance" in msg.lower() else "ERROR"
                print(f"    {tag}: {msg[:200]}")
            print()
    finally:
        db.close()
    print("done")


if __name__ == "__main__":
    main()
