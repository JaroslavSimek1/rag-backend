from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from models import SessionLocal, Source, ScheduleEnum, Base, engine
from ingestion import ingest_url

INTERVALS = {
    ScheduleEnum.HOURLY: timedelta(hours=1),
    ScheduleEnum.DAILY: timedelta(days=1),
    ScheduleEnum.WEEKLY: timedelta(weeks=1),
    ScheduleEnum.MONTHLY: timedelta(days=30),
}

scheduler = BackgroundScheduler()


def check_scheduled_sources():
    """Check all sources with a schedule and trigger ingestion if due."""
    db = SessionLocal()
    try:
        sources = (
            db.query(Source)
            .filter(Source.schedule_interval.isnot(None))
            .all()
        )
        now = datetime.now(timezone.utc)

        for source in sources:
            interval = INTERVALS.get(source.schedule_interval)
            if not interval:
                continue

            if source.last_scheduled_ts and (now - source.last_scheduled_ts) < interval:
                continue

            print(f"[Scheduler] Triggering scheduled ingest for source '{source.name}' ({source.base_url})")
            source.last_scheduled_ts = now
            db.commit()

            try:
                ingest_url(source.base_url, source.id, deep_crawl=False, max_depth=2)
            except Exception as e:
                print(f"[Scheduler] Error ingesting source {source.id}: {e}")

    except Exception as e:
        print(f"[Scheduler] Error in check_scheduled_sources: {e}")
    finally:
        db.close()


def start_scheduler():
    """Start the background scheduler, checking every 60 seconds."""
    Base.metadata.create_all(bind=engine)
    scheduler.add_job(
        check_scheduled_sources,
        "interval",
        seconds=60,
        id="source_scheduler",
        replace_existing=True,
    )
    scheduler.start()
    print("[Scheduler] Started — checking sources every 60s")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
