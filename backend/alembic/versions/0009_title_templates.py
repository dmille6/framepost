"""title_templates — user-defined title/description patterns with named slots

Each row is a reusable template like:
  name: "Performance"
  title_template: '{performer} performing at "{event}" at {venue} {city} / {date}'
  description_template: '{performer} on stage during {event} at {venue}, {city}.'
  fields_json: '[{"key":"performer","label":"Performer","placeholder":"Eddie Lockwood"}, ...]'

The metadata editor offers an "Apply template" action that opens a small form built from
the template's fields, then renders the templates into title and description.

Seeds three sensible defaults: Performance / Portrait / Landscape.

Revision ID: 0009_title_templates
Revises: 0008_reddit_posted
Create Date: 2026-05-04 19:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_title_templates"
down_revision: Union[str, None] = "0008_reddit_posted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "title_templates",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("title_template", sa.Text, nullable=False),
        sa.Column("description_template", sa.Text, nullable=True),
        sa.Column("fields_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )

    seeds = [
        {
            "id": "tpl_performance",
            "name": "Performance",
            "title_template": '{performer} performing at "{event}" at {venue} {city} / {date}',
            "description_template": '{performer} on stage during "{event}" at {venue}, {city}. {date}.',
            "fields_json": (
                '[{"key":"performer","label":"Performer","placeholder":"Eddie Lockwood"},'
                '{"key":"event","label":"Event","placeholder":"Jest"},'
                '{"key":"venue","label":"Venue","placeholder":"Allways Lounge"},'
                '{"key":"city","label":"City","placeholder":"New Orleans, LA"},'
                '{"key":"date","label":"Date","placeholder":"March 1 2024"}]'
            ),
            "sort_order": 0,
        },
        {
            "id": "tpl_portrait",
            "name": "Portrait",
            "title_template": "{subject} — {city} / {date}",
            "description_template": "Portrait of {subject}, {city}. {date}.",
            "fields_json": (
                '[{"key":"subject","label":"Subject","placeholder":"Person\'s name"},'
                '{"key":"city","label":"City","placeholder":"New Orleans, LA"},'
                '{"key":"date","label":"Date","placeholder":"March 1 2024"}]'
            ),
            "sort_order": 10,
        },
        {
            "id": "tpl_landscape",
            "name": "Landscape",
            "title_template": "{location} — {date}",
            "description_template": "{location}, {date}. {context}",
            "fields_json": (
                '[{"key":"location","label":"Location","placeholder":"e.g. Bayou Sauvage, LA"},'
                '{"key":"date","label":"Date","placeholder":"March 2024"},'
                '{"key":"context","label":"Context (optional)","placeholder":"sunset, golden hour, after the storm..."}]'
            ),
            "sort_order": 20,
        },
    ]
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO title_templates "
            "(id, name, title_template, description_template, fields_json, sort_order) VALUES "
            + ", ".join(
                f"(:id{i}, :name{i}, :title{i}, :desc{i}, :fields{i}, :sort{i})"
                for i in range(len(seeds))
            )
        ).bindparams(
            **{
                f"id{i}": s["id"] for i, s in enumerate(seeds)
            },
            **{f"name{i}": s["name"] for i, s in enumerate(seeds)},
            **{f"title{i}": s["title_template"] for i, s in enumerate(seeds)},
            **{f"desc{i}": s["description_template"] for i, s in enumerate(seeds)},
            **{f"fields{i}": s["fields_json"] for i, s in enumerate(seeds)},
            **{f"sort{i}": s["sort_order"] for i, s in enumerate(seeds)},
        )
    )


def downgrade() -> None:
    op.drop_table("title_templates")
