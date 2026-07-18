# database/indicator_db.py
"""Indicator engine persistence: scripts, immutable script versions, chart
layouts, alerts, and execution errors (architecture doc §16). Attached to the
main DATABASE_URL like flow_db (low write volume, per-user app data)."""

import logging
import os

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, scoped_session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import func

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL and "sqlite" in DATABASE_URL:
    engine = create_engine(
        DATABASE_URL, poolclass=NullPool, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(DATABASE_URL, pool_size=50, max_overflow=100, pool_timeout=10)

db_session = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
Base = declarative_base()
Base.query = db_session.query_property()


class IndicatorScript(Base):
    """A user-owned OpenScript indicator (current pointer + metadata)."""

    __tablename__ = "indicator_scripts"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    language = Column(String(32), nullable=False, default="openscript")
    current_version_id = Column(Integer, nullable=True)
    visibility = Column(String(16), nullable=False, default="private")  # private|shared|public|builtin
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    versions = relationship(
        "IndicatorScriptVersion", back_populates="script", cascade="all, delete-orphan"
    )


class IndicatorScriptVersion(Base):
    """Immutable source + server-compiled IR snapshot. Never overwritten."""

    __tablename__ = "indicator_script_versions"

    id = Column(Integer, primary_key=True)
    script_id = Column(Integer, ForeignKey("indicator_scripts.id"), nullable=False, index=True)
    version_number = Column(Integer, nullable=False)
    source_code = Column(Text, nullable=False)
    source_hash = Column(String(64), nullable=False)
    compiler_version = Column(String(32), nullable=False)
    compiled_ir = Column(JSON, nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    script = relationship("IndicatorScript", back_populates="versions")


class ChartLayout(Base):
    """Saved chart workspace state (active indicators, inputs, styles, panes)."""

    __tablename__ = "chart_layouts"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    symbol = Column(String(64), nullable=True)
    exchange = Column(String(32), nullable=True)
    timeframe = Column(String(16), nullable=True)
    layout_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IndicatorAlert(Base):
    """A persisted alertcondition subscription evaluated headlessly."""

    __tablename__ = "indicator_alerts"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    script_version_id = Column(Integer, ForeignKey("indicator_script_versions.id"), nullable=True)
    builtin_id = Column(String(64), nullable=True)  # builtin.* alerts without a script
    symbol = Column(String(64), nullable=False)
    exchange = Column(String(32), nullable=False)
    timeframe = Column(String(16), nullable=False)
    condition_id = Column(String(128), nullable=False)
    inputs_json = Column(JSON, nullable=True)
    trigger_mode = Column(String(16), nullable=False, default="bar-close")  # bar-close|intrabar
    is_active = Column(Boolean, nullable=False, default=True)
    last_evaluated_bar = Column(Integer, nullable=True)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IndicatorExecutionError(Base):
    """Headless execution failures for observability (doc §16)."""

    __tablename__ = "indicator_execution_errors"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(255), nullable=False, index=True)
    script_version_id = Column(Integer, nullable=True)
    symbol = Column(String(64), nullable=True)
    timeframe = Column(String(16), nullable=True)
    phase = Column(String(32), nullable=False)  # compile|execute|alert
    error_code = Column(String(16), nullable=True)
    message = Column(Text, nullable=False)
    bar_index = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


def init_db():
    """Create indicator engine tables (idempotent)."""
    from database.db_init_helper import init_db_with_logging

    init_db_with_logging(Base, engine, "Indicator DB", logger)
