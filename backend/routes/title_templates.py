"""Title template CRUD. Templates have named slots like `{performer}` rendered by the
metadata editor's Apply Template dialog. Stored as JSON-string fields list."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_session
from models import TitleTemplate, User
from routes.auth import current_user

router = APIRouter()


class TemplateField(BaseModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    placeholder: str | None = Field(default=None, max_length=200)


class TemplateOut(BaseModel):
    id: str
    name: str
    title_template: str
    description_template: str | None
    fields: list[TemplateField]
    sort_order: int


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    title_template: str = Field(min_length=1, max_length=600)
    description_template: str | None = Field(default=None, max_length=2000)
    fields: list[TemplateField] = Field(default_factory=list)
    sort_order: int = Field(default=0)


_PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def _validate_template_consistency(body: TemplateIn) -> None:
    """Make sure every {placeholder} in the templates corresponds to a declared field, and
    every declared field actually appears somewhere in either template. Catches typos early.
    """
    declared = {f.key for f in body.fields}
    used: set[str] = set()
    for tpl in (body.title_template, body.description_template or ""):
        used.update(_PLACEHOLDER_RE.findall(tpl))

    unknown = used - declared
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Templates reference placeholders {sorted(unknown)} that aren't declared as fields.",
        )
    # Unused fields are a soft warning, not an error — user might be staging fields for later.


def _to_out(t: TitleTemplate) -> TemplateOut:
    try:
        fields_raw = json.loads(t.fields_json or "[]")
        if not isinstance(fields_raw, list):
            fields_raw = []
    except (TypeError, ValueError):
        fields_raw = []
    return TemplateOut(
        id=t.id,
        name=t.name,
        title_template=t.title_template,
        description_template=t.description_template,
        fields=[TemplateField(**f) for f in fields_raw],
        sort_order=t.sort_order or 0,
    )


@router.get("", response_model=list[TemplateOut])
def list_templates(
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    rows = db.execute(
        select(TitleTemplate).order_by(TitleTemplate.sort_order.asc(), TitleTemplate.name.asc())
    ).scalars().all()
    return [_to_out(r) for r in rows]


@router.post("", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
def create_template(
    body: TemplateIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    _validate_template_consistency(body)
    t = TitleTemplate(
        id=f"tpl_{uuid.uuid4().hex[:12]}",
        name=body.name.strip(),
        title_template=body.title_template,
        description_template=body.description_template,
        fields_json=json.dumps([f.model_dump() for f in body.fields]),
        sort_order=body.sort_order,
        created_at=datetime.now(timezone.utc),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _to_out(t)


@router.put("/{template_id}", response_model=TemplateOut)
def update_template(
    template_id: str,
    body: TemplateIn,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
):
    t = db.get(TitleTemplate, template_id)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    _validate_template_consistency(body)
    t.name = body.name.strip()
    t.title_template = body.title_template
    t.description_template = body.description_template
    t.fields_json = json.dumps([f.model_dump() for f in body.fields])
    t.sort_order = body.sort_order
    db.commit()
    db.refresh(t)
    return _to_out(t)


@router.delete("/{template_id}", status_code=status.HTTP_200_OK)
def delete_template(
    template_id: str,
    db: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    t = db.get(TitleTemplate, template_id)
    if not t:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "template not found")
    db.delete(t)
    db.commit()
    return {"ok": True, "removed": template_id}
