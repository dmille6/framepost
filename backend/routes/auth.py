"""Auth endpoints: login, logout, me."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from database import SessionLocal
from models import User
from services.auth import (
    SESSION_COOKIE,
    hash_password,
    lookup_user,
    session_timeout_seconds,
    sign_session,
    verify_password,
    verify_session,
)

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UserOut(BaseModel):
    id: int
    username: str

    class Config:
        from_attributes = True


def current_user(request: Request) -> User:
    payload = verify_session(request.cookies.get(SESSION_COOKIE))
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    user = lookup_user(payload["uid"])
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")
    return user


@router.post("/login")
def login(body: LoginRequest, response: Response):
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
        token = sign_session(user.id)
    finally:
        db.close()

    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=session_timeout_seconds(),
        httponly=True,
        samesite="lax",
        secure=False,  # LAN-only HTTP per brief
        path="/",
    )
    return {"id": user.id, "username": user.username}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(current_user),
):
    if len(body.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "new password must be ≥ 8 characters")
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "current password is incorrect")
    db = SessionLocal()
    try:
        u = db.get(User, user.id)
        if not u:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
        u.password_hash = hash_password(body.new_password)
        db.commit()
    finally:
        db.close()
    return {"ok": True}
