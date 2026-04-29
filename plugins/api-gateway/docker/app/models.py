from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PlaylistPayload(BaseModel):
    name: str
    tracks: list[str] = []


class PlaylistAddTracksPayload(BaseModel):
    name: str
    track_ids: list[str]
    force: bool = False


class MetadataUpdatePayload(BaseModel):
    title: Optional[str] = ""
    artist: Optional[str] = ""
    album: Optional[str] = ""
    year: Optional[str] = ""
    art_link: Optional[str] = ""
    art_upload_data: Optional[str] = ""


class ArtistImagePayload(BaseModel):
    image_link: Optional[str] = ""
    upload_data: Optional[str] = ""
