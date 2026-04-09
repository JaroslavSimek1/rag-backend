"""Tests for database models."""
import pytest
from datetime import datetime, timezone
from models import Source, IngestJob, Evidence, StatusEnum, StrategyEnum, ScheduleEnum


def test_create_source(db_session):
    source = Source(name="TestSource", base_url="https://example.com", permission_type="public")
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    assert source.id is not None
    assert source.name == "TestSource"
    assert source.permission_type == "public"


def test_create_ingest_job(db_session):
    source = Source(name="JobTestSource", base_url="https://example.com")
    db_session.add(source)
    db_session.commit()

    job = IngestJob(
        source_id=source.id,
        url="https://example.com/page",
        strategy=StrategyEnum.HTML,
        status=StatusEnum.RUNNING,
        max_depth=2,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    assert job.id is not None
    assert job.status == StatusEnum.RUNNING
    assert job.strategy == StrategyEnum.HTML


def test_create_evidence(db_session):
    source = Source(name="EvidenceTestSource", base_url="https://example.com")
    db_session.add(source)
    db_session.commit()

    job = IngestJob(source_id=source.id, url="https://example.com", strategy=StrategyEnum.HTML)
    db_session.add(job)
    db_session.commit()

    evidence = Evidence(
        job_id=job.id,
        evidence_type="screenshot",
        storage_uri="/tmp/test.png",
        file_hash="abc123def456",
    )
    db_session.add(evidence)
    db_session.commit()

    assert evidence.id is not None
    assert evidence.file_hash == "abc123def456"


def test_status_enum_values():
    assert StatusEnum.PENDING.value == "PENDING"
    assert StatusEnum.COMPLETED.value == "COMPLETED"
    assert StatusEnum.CAPTCHA_DETECTED.value == "CAPTCHA_DETECTED"


def test_strategy_enum_values():
    assert StrategyEnum.HTML.value == "HTML"
    assert StrategyEnum.SCREENSHOT.value == "Screenshot"
    assert StrategyEnum.RENDER.value == "Rendered DOM"


def test_schedule_enum_values():
    assert ScheduleEnum.HOURLY.value == "hourly"
    assert ScheduleEnum.DAILY.value == "daily"
    assert ScheduleEnum.WEEKLY.value == "weekly"
    assert ScheduleEnum.MONTHLY.value == "monthly"


def test_source_schedule(db_session):
    source = Source(
        name="ScheduledSource",
        base_url="https://example.com",
        schedule_interval=ScheduleEnum.DAILY,
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    assert source.schedule_interval == ScheduleEnum.DAILY


def test_job_relationship(db_session):
    source = Source(name="RelTestSource", base_url="https://example.com")
    db_session.add(source)
    db_session.commit()

    job = IngestJob(source_id=source.id, url="https://example.com", strategy=StrategyEnum.HTML, status=StatusEnum.COMPLETED)
    db_session.add(job)
    db_session.commit()

    ev = Evidence(job_id=job.id, evidence_type="markdown", storage_uri="/tmp/test.md", file_hash="hash123")
    db_session.add(ev)
    db_session.commit()

    db_session.refresh(job)
    assert len(job.evidences) >= 1
