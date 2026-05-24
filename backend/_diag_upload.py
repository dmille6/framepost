"""Direct Flickr upload attempt with full request/response capture."""
from pathlib import Path

from sqlalchemy import select

from database import SessionLocal
from models import Post
from services import image, storage
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
        derivative = storage.DERIVATIVES / f"_diag_{p.id}.jpg"
        image.make_derivative(src, derivative, 2048)
        print(f"derivative: {derivative.stat().st_size} bytes")

        client = flickr_svc.load_oauth_session(db)
        client.event_hooks = {
            "request": [_log_request],
            "response": [_log_response],
        }

        photo_bytes = derivative.read_bytes()
        files = {"photo": (derivative.name, photo_bytes, "image/jpeg")}
        print("\n→ posting...")
        response = client.post(
            flickr_svc.UPLOAD_URL,
            data={"is_public": "0", "hidden": "2", "is_friend": "0", "is_family": "0"},
            files=files,
            timeout=300.0,
        )
        print(f"\nresponse body:\n{response.text}")
        derivative.unlink(missing_ok=True)
    finally:
        db.close()


def _log_request(request) -> None:
    print(f"\n--- REQUEST ---")
    print(f"  {request.method} {request.url}")
    for k, v in request.headers.items():
        if k.lower() == "authorization":
            print(f"  {k}: {v[:80]}…")
        else:
            print(f"  {k}: {v}")
    body_len = len(request.read()) if hasattr(request, "read") else 0
    print(f"  body length (after read): {body_len}")


def _log_response(response) -> None:
    print(f"\n--- RESPONSE ---")
    print(f"  HTTP {response.status_code}")
    for k, v in response.headers.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
