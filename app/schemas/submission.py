from pydantic import BaseModel, EmailStr
from typing import Optional


class DraftCreate(BaseModel):
    artist_name: Optional[str] = None
    email: Optional[EmailStr] = None
    track_title: Optional[str] = None
    genre: Optional[str] = None
    lyrics: Optional[str] = None


class DraftUpdate(BaseModel):
    artist_name: Optional[str] = None
    email: Optional[EmailStr] = None
    track_title: Optional[str] = None
    genre: Optional[str] = None
    lyrics: Optional[str] = None
