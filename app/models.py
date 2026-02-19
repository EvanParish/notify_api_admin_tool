from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    and_,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, foreign

from .db import Base


class Service(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    environment: Mapped[str] = mapped_column(
        String, primary_key=True, default="unknown", index=True
    )
    name: Mapped[str] = mapped_column(String, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)

    # Additional fields from API
    message_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rate_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    research_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    count_as_live: Mapped[bool] = mapped_column(Boolean, default=True)
    prefix_sms: Mapped[bool] = mapped_column(Boolean, default=False)
    email_from: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    permissions: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON array as text
    organisation_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    crown: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    go_live_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    templates: Mapped[list["Template"]] = relationship(
        back_populates="service",
        primaryjoin=lambda: and_(
            Service.id == foreign(Template.service_id),
            Service.environment == foreign(Template.environment),
        ),
    )


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    environment: Mapped[str] = mapped_column(String, default="unknown", index=True)
    service_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    template_type: Mapped[str] = mapped_column(
        Enum("email", "sms", name="template_type")
    )
    content: Mapped[str] = mapped_column(Text)
    subject: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Additional fields from API
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    process_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reply_to_email: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    service: Mapped[Service] = relationship(
        back_populates="templates",
        primaryjoin=lambda: and_(
            Service.id == foreign(Template.service_id),
            Service.environment == foreign(Template.environment),
        ),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    environment: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    service_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    key_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    expiry_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class SmsSender(Base):
    __tablename__ = "sms_senders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    environment: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"))
    sms_sender: Mapped[str] = mapped_column(String)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    inbound_number_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rate_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rate_limit_interval: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sms_sender_specifics: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    environment: Mapped[str] = mapped_column(
        String, primary_key=True, default="unknown", index=True
    )
    email_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mobile_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    failed_login_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    logged_in_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    password_changed_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    current_session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    identity_provider_user_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    additional_information: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    permissions: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    services: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    organisations: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)


class ProviderDetail(Base):
    __tablename__ = "provider_details"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    environment: Mapped[str] = mapped_column(
        String, primary_key=True, default="unknown", index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    current_month_billable_sms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )
    display_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    identifier: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    load_balancing_weight: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notification_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    priority: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    supports_international: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    updated_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class LocalApiKey(Base):
    __tablename__ = "local_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(String)
    environment: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    key_name: Mapped[str] = mapped_column(String)
    key_secret: Mapped[str] = mapped_column(Text)
    key_type: Mapped[str] = mapped_column(
        Enum("normal", "team", "test", name="key_type")
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
