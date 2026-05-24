"""initial schema + seed app_config

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-03 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("last_login_at", sa.DateTime),
    )

    op.create_table(
        "posts",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("title", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("tags", sa.Text),
        sa.Column("scheduled_at", sa.DateTime),
        sa.Column("posted_at", sa.DateTime),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("original_filename", sa.Text),
        sa.Column("original_path", sa.Text),
        sa.Column("thumbnail_path", sa.Text),
        sa.Column("file_size_bytes", sa.Integer),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("sha256", sa.String),
        sa.Column("captured_at", sa.DateTime),
        sa.Column("camera_make", sa.Text),
        sa.Column("camera_model", sa.Text),
        sa.Column("lens", sa.Text),
        sa.Column("focal_length", sa.Float),
        sa.Column("iso", sa.Integer),
        sa.Column("shutter_speed", sa.String),
        sa.Column("aperture", sa.Float),
        sa.Column("gps_lat", sa.Float),
        sa.Column("gps_lng", sa.Float),
        sa.Column("exif_raw", sa.Text),
        sa.Column("iptc_raw", sa.Text),
        sa.Column("privacy", sa.String, server_default="private"),
        sa.Column("safety_level", sa.String, server_default="safe"),
        sa.Column("content_type", sa.String, server_default="photo"),
        sa.Column("flickr_photo_id", sa.String),
        sa.Column("flickr_url", sa.Text),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
    )
    op.create_index("idx_posts_sha256", "posts", ["sha256"])
    op.create_index("idx_posts_status", "posts", ["status"])
    op.create_index("idx_posts_scheduled_at", "posts", ["scheduled_at"])
    op.create_index("idx_posts_next_retry_at", "posts", ["next_retry_at"])
    op.create_index("idx_posts_flickr_photo_id", "posts", ["flickr_photo_id"])

    op.create_table(
        "post_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("event_type", sa.String, nullable=False),
        sa.Column("actor", sa.String, nullable=False),
        sa.Column("details", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_post_events_post_id", "post_events", ["post_id"])
    op.create_index("idx_post_events_created_at", "post_events", ["created_at"])

    op.create_table(
        "tag_profiles",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("tags", sa.Text, nullable=False, server_default=""),
        sa.Column("is_default", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
    )

    op.create_table(
        "post_profiles",
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("profile_id", sa.String, nullable=False),
        sa.PrimaryKeyConstraint("post_id", "profile_id"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["tag_profiles.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "albums",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("flickr_album_id", sa.String),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("photo_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_synced_at", sa.DateTime),
    )

    op.create_table(
        "post_albums",
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("album_id", sa.String, nullable=False),
        sa.PrimaryKeyConstraint("post_id", "album_id"),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["album_id"], ["albums.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "groups",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("flickr_group_id", sa.String),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("category", sa.String),
        sa.Column("daily_limit", sa.Integer),
        sa.Column("content_notes", sa.Text),
        sa.Column("no_watermark", sa.Integer, nullable=False, server_default="0"),
        sa.Column("default_enabled", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
    )

    op.create_table(
        "post_groups",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("post_id", sa.String, nullable=False),
        sa.Column("group_id", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("submitted_at", sa.DateTime),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime),
        sa.Column("error_message", sa.Text),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_post_groups_post_id", "post_groups", ["post_id"])
    op.create_index("idx_post_groups_group_id", "post_groups", ["group_id"])
    op.create_index("idx_post_groups_next_retry_at", "post_groups", ["next_retry_at"])

    op.create_table(
        "flickr_photos",
        sa.Column("flickr_photo_id", sa.String, primary_key=True),
        sa.Column("title", sa.Text),
        sa.Column("machine_tags", sa.Text),
        sa.Column("date_taken", sa.DateTime),
        sa.Column("date_uploaded", sa.DateTime),
        sa.Column("url", sa.Text),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("album_ids", sa.Text),
        sa.Column("last_synced_at", sa.DateTime),
    )
    op.create_index("idx_flickr_photos_title", "flickr_photos", ["title"])
    op.create_index("idx_flickr_photos_date_taken", "flickr_photos", ["date_taken"])

    op.create_table(
        "platform_credentials",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("platform", sa.String, nullable=False),
        sa.Column("access_token", sa.Text),
        sa.Column("refresh_token", sa.Text),
        sa.Column("token_expires", sa.DateTime),
        sa.Column("account_name", sa.Text),
        sa.Column("key_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("connected_at", sa.DateTime, nullable=False, server_default=sa.func.current_timestamp()),
    )

    op.create_table(
        "app_config",
        sa.Column("key", sa.String, primary_key=True),
        sa.Column("value", sa.Text),
    )

    seeds = [
        ("timezone", "America/Chicago"),
        ("studio_name", "Darrell Miller Photography"),
        ("theme", "dark"),
        ("start_page", "draft_queue"),
        ("session_timeout_minutes", "1440"),
        ("photo_root", "/mnt/photo-data"),
        ("upload_max_mb", "200"),
        ("original_retention_days", "30"),
        ("storage_warning_percent", "80"),
        ("storage_hardstop_gb", "5"),
        ("cleanup_time", "03:00"),
        ("flickr_sync_time", "04:00"),
        ("ai_tagging_enabled", "false"),
        ("ai_max_suggestions", "10"),
        ("ai_send_full_resolution", "false"),
        ("watch_folder_enabled", "false"),
        ("watch_folder_path", ""),
        ("default_privacy", "public"),
        ("default_safety_level", "safe"),
        ("default_content_type", "photo"),
        ("default_publish_time", "10:00"),
        ("max_groups_default", "5"),
        ("warn_groups_threshold", "8"),
        ("schedule_fuzz_minutes", "5"),
        ("retry_max_attempts", "5"),
        ("retry_backoff_minutes", "1,5,15,60,240"),
        ("worker_last_heartbeat", ""),
    ]
    config = sa.table("app_config", sa.column("key", sa.String), sa.column("value", sa.Text))
    op.bulk_insert(config, [{"key": k, "value": v} for k, v in seeds])


def downgrade() -> None:
    op.drop_table("app_config")
    op.drop_table("platform_credentials")
    op.drop_index("idx_flickr_photos_date_taken", table_name="flickr_photos")
    op.drop_index("idx_flickr_photos_title", table_name="flickr_photos")
    op.drop_table("flickr_photos")
    op.drop_index("idx_post_groups_next_retry_at", table_name="post_groups")
    op.drop_index("idx_post_groups_group_id", table_name="post_groups")
    op.drop_index("idx_post_groups_post_id", table_name="post_groups")
    op.drop_table("post_groups")
    op.drop_table("groups")
    op.drop_table("post_albums")
    op.drop_table("albums")
    op.drop_table("post_profiles")
    op.drop_table("tag_profiles")
    op.drop_index("idx_post_events_created_at", table_name="post_events")
    op.drop_index("idx_post_events_post_id", table_name="post_events")
    op.drop_table("post_events")
    op.drop_index("idx_posts_flickr_photo_id", table_name="posts")
    op.drop_index("idx_posts_next_retry_at", table_name="posts")
    op.drop_index("idx_posts_scheduled_at", table_name="posts")
    op.drop_index("idx_posts_status", table_name="posts")
    op.drop_index("idx_posts_sha256", table_name="posts")
    op.drop_table("posts")
    op.drop_table("users")
