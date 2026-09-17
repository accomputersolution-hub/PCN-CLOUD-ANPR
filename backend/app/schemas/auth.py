from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import UserRole
from app.schemas.common import ORMModel


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class UserPublic(ORMModel):
    id: str
    email: str
    full_name: str
    role: UserRole
    organization_id: str | None
    is_active: bool
    last_login_at: datetime | None = None
    site_ids: list[str] = []


class LoginResponse(TokenResponse):
    user: UserPublic


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    role: UserRole
    organization_id: str | None = None
    site_ids: list[str] = []


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None
    site_ids: list[str] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
