"""Abstract platform interface. v1 has only Flickr; the abstraction is here so the boundary is
visible, not because we plan to add a second platform without a focused effort + schema migration.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PlatformClient(ABC):
    @abstractmethod
    def upload(self, *, image_path: str, post: Any) -> str:
        """Upload an image and return the platform photo id."""

    @abstractmethod
    def add_to_album(self, *, photo_id: str, album_id: str) -> None: ...

    @abstractmethod
    def submit_to_group(self, *, photo_id: str, group_id: str) -> None: ...

    @abstractmethod
    def list_albums(self) -> list[dict]: ...

    @abstractmethod
    def list_my_photos(self, since_iso: str | None = None) -> list[dict]: ...
