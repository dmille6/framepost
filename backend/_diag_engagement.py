"""Find what happened to photo 55244869417."""
import xml.etree.ElementTree as ET

from database import SessionLocal
from services.platforms import flickr


def main() -> None:
    db = SessionLocal()
    try:
        # Page through all the user's photos to look for our ID
        target = "55244869417"
        print(f"Searching all user photos for {target}...")
        for page in range(1, 6):
            try:
                root = flickr.rest_call(
                    db, "flickr.people.getPhotos",
                    user_id="me", per_page="500", page=str(page),
                )
                photos = root.findall(".//photo")
                ids = [ph.get("id") for ph in photos]
                if target in ids:
                    ph = next(p for p in photos if p.get("id") == target)
                    print(f"  FOUND on page {page}: title={ph.get('title')} ispublic={ph.get('ispublic')}")
                    break
                else:
                    print(f"  page {page}: {len(ids)} photos, IDs range {ids[0] if ids else '-'}..{ids[-1] if ids else '-'}")
                if not photos:
                    break
            except Exception as e:
                print(f"  page {page} error: {e}")
                break
        else:
            print(f"  not found in first 5 pages of recent photos")

        # Try getSizes — works on any visible photo
        print(f"\nTrying flickr.photos.getSizes for {target}...")
        try:
            root = flickr.rest_call(db, "flickr.photos.getSizes", photo_id=target)
            print(ET.tostring(root, encoding="unicode")[:400])
        except Exception as e:
            print(f"  error: {e}")

        # Try flickr.photos.recentlyUpdated to see if it shows up there
        print("\nMost recent uploads (recentlyUpdated):")
        try:
            root = flickr.rest_call(
                db, "flickr.photos.recentlyUpdated", min_date="1", per_page="10",
            )
            for ph in root.findall(".//photo"):
                print(f"  id={ph.get('id')} title={ph.get('title')}")
        except Exception as e:
            print(f"  error: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
