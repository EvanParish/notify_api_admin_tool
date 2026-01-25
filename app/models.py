from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Service(Base):
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String, primary_key=True)
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
    permissions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON array as text
    organisation_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    crown: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    go_live_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    templates: Mapped[list["Template"]] = relationship(back_populates="service")


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    service_id: Mapped[str] = mapped_column(ForeignKey("services.id"))
    name: Mapped[str] = mapped_column(String)
    template_type: Mapped[str] = mapped_column(Enum("email", "sms", "letter", name="template_type"))
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

    service: Mapped[Service] = relationship(back_populates="templates")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email_address: Mapped[str] = mapped_column(String, index=True)
    auth_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Additional fields from API
    mobile_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    service_id: Mapped[Optional[str]] = mapped_column(ForeignKey("services.id"), nullable=True)
    name: Mapped[str] = mapped_column(String)
    key_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    expiry_date: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class LocalApiKey(Base):
    __tablename__ = "local_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(String)
    key_name: Mapped[str] = mapped_column(String)
    key_secret: Mapped[str] = mapped_column(Text)
    key_type: Mapped[str] = mapped_column(Enum("normal", "team", "test", name="key_type"))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
