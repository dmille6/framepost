from sqlalchemy import select
from database import SessionLocal
from models import Post

db = SessionLocal()
for p in db.execute(select(Post).order_by(Post.created_at.desc()).limit(3)).scalars().all():
    print(f"  {p.id[:8]}  {(p.original_filename or '(no name)')[:50]:50}"
          f"  status={p.status}  iptc={bool(p.iptc_raw)}  title={p.title!r}")
db.close()
