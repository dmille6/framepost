from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)

from database import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())
    last_login_at = Column(DateTime)


class Post(Base):
    __tablename__ = "posts"
    id = Column(String, primary_key=True)
    title = Column(Text)
    description = Column(Text)
    tags = Column(Text)
    scheduled_at = Column(DateTime)
    posted_at = Column(DateTime)
    status = Column(String, nullable=False, server_default="pending")
    original_filename = Column(Text)
    original_path = Column(Text)
    thumbnail_path = Column(Text)
    file_size_bytes = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)
    sha256 = Column(String, index=True)
    captured_at = Column(DateTime)
    camera_make = Column(Text)
    camera_model = Column(Text)
    lens = Column(Text)
    focal_length = Column(Float)
    iso = Column(Integer)
    shutter_speed = Column(String)
    aperture = Column(Float)
    gps_lat = Column(Float)
    gps_lng = Column(Float)
    exif_raw = Column(Text)
    iptc_raw = Column(Text)
    privacy = Column(String, server_default="private")
    safety_level = Column(String, server_default="safe")
    content_type = Column(String, server_default="photo")
    flickr_photo_id = Column(String, index=True)
    flickr_url = Column(Text)
    retry_count = Column(Integer, nullable=False, server_default="0")
    next_retry_at = Column(DateTime)
    error_message = Column(Text)
    posted_to_instagram_at = Column(DateTime)
    reddit_posted_at = Column(DateTime)
    target_platforms = Column(Text)  # JSON list, null = use defaults
    created_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())
    updated_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())


class PostEvent(Base):
    __tablename__ = "post_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    details = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.current_timestamp(), index=True)


class PostComment(Base):
    __tablename__ = "post_comments"
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String, nullable=False)
    remote_id = Column(Text, nullable=False)
    author_handle = Column(Text)
    author_display_name = Column(Text)
    author_url = Column(Text)
    body = Column(Text, nullable=False, server_default="")
    posted_at = Column(DateTime)
    fetched_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())
    seen_at = Column(DateTime)


class PostLike(Base):
    __tablename__ = "post_likes"
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String, nullable=False)
    remote_id = Column(Text, nullable=False)
    actor_handle = Column(Text)
    actor_display_name = Column(Text)
    actor_url = Column(Text)
    liked_at = Column(DateTime)
    fetched_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())
    seen_at = Column(DateTime)


class EngagementSnapshot(Base):
    __tablename__ = "engagement_snapshots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String, nullable=False)
    sampled_at = Column(DateTime, nullable=False, server_default=func.current_timestamp(), index=True)
    views = Column(Integer, nullable=False, server_default="0")
    likes = Column(Integer, nullable=False, server_default="0")
    comments_count = Column(Integer, nullable=False, server_default="0")
    reposts = Column(Integer, nullable=False, server_default="0")


class TitleTemplate(Base):
    __tablename__ = "title_templates"
    id = Column(String, primary_key=True)
    name = Column(Text, nullable=False)
    title_template = Column(Text, nullable=False)
    description_template = Column(Text)
    fields_json = Column(Text, nullable=False, server_default="[]")
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())


class TagProfile(Base):
    __tablename__ = "tag_profiles"
    id = Column(String, primary_key=True)
    name = Column(Text, nullable=False)
    tags = Column(Text, nullable=False, server_default="")
    is_default = Column(Integer, nullable=False, server_default="0")
    sort_order = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())


class PostProfile(Base):
    __tablename__ = "post_profiles"
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    profile_id = Column(String, ForeignKey("tag_profiles.id", ondelete="CASCADE"), primary_key=True)


class Album(Base):
    __tablename__ = "albums"
    id = Column(String, primary_key=True)
    flickr_album_id = Column(String)
    name = Column(Text, nullable=False)
    description = Column(Text)
    photo_count = Column(Integer, nullable=False, server_default="0")
    last_synced_at = Column(DateTime)


class PostAlbum(Base):
    __tablename__ = "post_albums"
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    album_id = Column(String, ForeignKey("albums.id", ondelete="CASCADE"), primary_key=True)


class Group(Base):
    __tablename__ = "groups"
    id = Column(String, primary_key=True)
    flickr_group_id = Column(String)
    name = Column(Text, nullable=False)
    category = Column(String)
    daily_limit = Column(Integer)
    content_notes = Column(Text)
    no_watermark = Column(Integer, nullable=False, server_default="0")
    default_enabled = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())


class PostGroup(Base):
    __tablename__ = "post_groups"
    id = Column(String, primary_key=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(String, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, nullable=False, server_default="pending")
    submitted_at = Column(DateTime)
    retry_count = Column(Integer, nullable=False, server_default="0")
    next_retry_at = Column(DateTime, index=True)
    error_message = Column(Text)


class FlickrPhoto(Base):
    __tablename__ = "flickr_photos"
    flickr_photo_id = Column(String, primary_key=True)
    title = Column(Text, index=True)
    machine_tags = Column(Text)
    date_taken = Column(DateTime, index=True)
    date_uploaded = Column(DateTime)
    url = Column(Text)
    width = Column(Integer)
    height = Column(Integer)
    album_ids = Column(Text)
    last_synced_at = Column(DateTime)


class PlatformCredential(Base):
    __tablename__ = "platform_credentials"
    id = Column(String, primary_key=True)
    platform = Column(String, nullable=False)
    access_token = Column(Text)
    refresh_token = Column(Text)
    token_expires = Column(DateTime)
    account_name = Column(Text)
    instance_url = Column(Text)
    extra_json = Column(Text)
    default_target = Column(Integer, nullable=False, server_default="1")
    last_success_at = Column(DateTime)
    last_error = Column(Text)
    key_version = Column(Integer, nullable=False, server_default="1")
    connected_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())


class PostPlatform(Base):
    __tablename__ = "post_platforms"
    post_id = Column(String, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    platform_id = Column(
        String, ForeignKey("platform_credentials.id", ondelete="CASCADE"), primary_key=True
    )
    status = Column(String, nullable=False, server_default="pending")
    remote_id = Column(Text)
    remote_url = Column(Text)
    posted_at = Column(DateTime)
    error_message = Column(Text)
    retry_count = Column(Integer, nullable=False, server_default="0")
    next_retry_at = Column(DateTime)


class AppConfig(Base):
    __tablename__ = "app_config"
    key = Column(String, primary_key=True)
    value = Column(Text)


class DiskSample(Base):
    __tablename__ = "disk_samples"
    id = Column(Integer, primary_key=True, autoincrement=True)
    sampled_at = Column(
        DateTime, nullable=False, server_default=func.current_timestamp(), index=True
    )
    total_bytes = Column(Integer, nullable=False)
    used_bytes = Column(Integer, nullable=False)
    free_bytes = Column(Integer, nullable=False)


class TrendingTag(Base):
    __tablename__ = "trending_tags"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)
    seed_tag = Column(String, nullable=False, index=True)
    tag = Column(String, nullable=False, index=True)
    score = Column(Float, nullable=False, server_default="1.0")
    last_synced_at = Column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )


class Reel(Base):
    __tablename__ = "reels"
    id = Column(String, primary_key=True)
    cover_post_id = Column(String, ForeignKey("posts.id", ondelete="RESTRICT"), nullable=False)
    total_duration_seconds = Column(Float, nullable=False, server_default="60.0")
    caption = Column(Text)
    mp4_path = Column(Text)
    status = Column(String, nullable=False, server_default="pending")
    error_message = Column(Text)
    created_at = Column(DateTime, nullable=False, server_default=func.current_timestamp(), index=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.current_timestamp())


class ReelPhoto(Base):
    __tablename__ = "reel_photos"
    reel_id = Column(String, ForeignKey("reels.id", ondelete="CASCADE"), primary_key=True)
    position = Column(Integer, primary_key=True)
    post_id = Column(String, ForeignKey("posts.id", ondelete="RESTRICT"), nullable=False, index=True)
    crop_start_json = Column(Text)
    crop_end_json = Column(Text)


class FlickrEngagement(Base):
    __tablename__ = "flickr_engagement"
    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(
        String, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    flickr_photo_id = Column(String, nullable=False)
    sampled_at = Column(
        DateTime, nullable=False, server_default=func.current_timestamp(), index=True
    )
    views = Column(Integer, nullable=False, server_default="0")
    faves = Column(Integer, nullable=False, server_default="0")
    comments = Column(Integer, nullable=False, server_default="0")
