import os
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    Enum,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime, timezone
import enum

Base = declarative_base()


class StrategyEnum(enum.Enum):
    API = "API"
    HTML = "HTML"
    RENDER = "Rendered DOM"
    SCREENSHOT = "Screenshot"
    UPSTREAM = "Upstream AI"


class StatusEnum(enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CAPTCHA_DETECTED = "CAPTCHA_DETECTED"


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    base_url = Column(String)
    permission_type = Column(String, default="public")
    crawl_rules = Column(Text, nullable=True)
    retention_rules = Column(String, default="30_days")

    jobs = relationship("IngestJob", back_populates="source")


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id = Column(Integer, primary_key=True, index=True)
    source_id = Column(Integer, ForeignKey("sources.id"))
    url = Column(String, index=True)
    strategy = Column(Enum(StrategyEnum))
    status = Column(Enum(StatusEnum), default=StatusEnum.PENDING)

    started_ts = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_ts = Column(DateTime, nullable=True)
    max_depth = Column(Integer, default=1)
    error_code = Column(String, nullable=True)
    captured_html_path = Column(String, nullable=True)

    source = relationship("Source", back_populates="jobs")
    evidences = relationship("Evidence", back_populates="job")


class Evidence(Base):
    __tablename__ = "evidences"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("ingest_jobs.id"))
    evidence_type = Column(String)
    storage_uri = Column(String)
    file_hash = Column(String)
    created_ts = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    job = relationship("IngestJob", back_populates="evidences")


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rag_storage.db")


def init_db():
    connect_args = (
        {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    )
    engine = create_engine(DATABASE_URL, connect_args=connect_args)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()
