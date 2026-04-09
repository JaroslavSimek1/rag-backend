import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from models import get_db, Source, IngestJob, StatusEnum, Evidence, ScheduleEnum, StrategyEnum
from ingestion import ingest_url
from rag import query_rag, delete_job_vectors
from auth import get_current_user, get_current_admin, UserInfo
from scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Web Data Ingestion API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth info (Keycloak handles login/register/user management) ──────────


@app.get("/api/auth/me")
def get_me(user: UserInfo = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


# ── Ingestion endpoints (admin only) ─────────────────────────────────────


class IngestRequest(BaseModel):
    url: str
    source_name: str = "DefaultSource"
    deep_crawl: bool = False
    max_depth: int = 1
    schedule: Optional[str] = None
    permission_type: str = "public"
    strategy: Optional[str] = None


class JobResponse(BaseModel):
    message: str
    source_id: int
    status: str = "started"


@app.post("/api/ingest", response_model=JobResponse)
def trigger_ingestion(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    source = db.query(Source).filter(Source.name == request.source_name).first()
    if not source:
        source = Source(
            name=request.source_name,
            base_url=request.url,
            permission_type=request.permission_type,
        )
        db.add(source)
        db.commit()
        db.refresh(source)
    elif request.permission_type != "public":
        source.permission_type = request.permission_type
        db.commit()

    if request.schedule:
        try:
            source.schedule_interval = ScheduleEnum(request.schedule)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid schedule: {request.schedule}. Use: hourly, daily, weekly, monthly",
            )
        db.commit()
    elif request.schedule is not None:
        source.schedule_interval = None
        db.commit()

    existing_job = (
        db.query(IngestJob)
        .filter(IngestJob.url == request.url, IngestJob.status == StatusEnum.COMPLETED)
        .order_by(IngestJob.max_depth.desc())
        .first()
    )

    if existing_job and existing_job.max_depth >= request.max_depth:
        return JobResponse(
            message=f"URL {request.url} already ingested with depth {existing_job.max_depth}. Skipping.",
            source_id=source.id,
            status="skipped",
        )

    background_tasks.add_task(
        ingest_url, request.url, source.id, request.deep_crawl, request.max_depth,
        request.strategy,
    )

    status_msg = "updated" if existing_job else "started"
    return JobResponse(
        message=f"Ingestion {status_msg} for {request.url}",
        source_id=source.id,
        status=status_msg,
    )


@app.get("/api/jobs")
def get_jobs(
    limit: int = 10,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    jobs = db.query(IngestJob).order_by(IngestJob.started_ts.desc()).limit(limit).all()

    result = []
    for j in jobs:
        evidences = db.query(Evidence).filter(Evidence.job_id == j.id).all()
        screenshots = [e for e in evidences if e.evidence_type == "screenshot"]
        result.append(
            {
                "id": j.id,
                "url": j.url,
                "status": j.status.value,
                "strategy": j.strategy.value if j.strategy else None,
                "error_code": j.error_code,
                "started_ts": j.started_ts,
                "completed_ts": j.completed_ts,
                "max_depth": j.max_depth,
                "has_evidence": len(evidences) > 0,
                "screenshot_count": len(screenshots),
            }
        )
    return {"jobs": result}


@app.delete("/api/jobs/{job_id}")
def delete_job(
    job_id: int,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    job = db.query(IngestJob).filter(IngestJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    try:
        delete_job_vectors(job_id)
    except Exception as e:
        print(f"Warning: Failed to delete vectors for job {job_id}: {e}")

    evidences = db.query(Evidence).filter(Evidence.job_id == job_id).all()
    for ev in evidences:
        if ev.storage_uri and os.path.exists(ev.storage_uri):
            try:
                os.remove(ev.storage_uri)
            except Exception:
                pass
        db.delete(ev)

    db.delete(job)
    db.commit()

    return {"message": f"Job {job_id} deleted successfully"}


@app.get("/api/jobs/{job_id}/files")
def get_job_files(
    job_id: int,
    admin: UserInfo = Depends(get_current_admin),
):
    data_dir = os.getenv("DATA_DIR", "data")
    if not os.path.exists(data_dir):
        return {"files": []}

    files = []
    prefix = f"job_{job_id}_"
    for filename in os.listdir(data_dir):
        if filename.startswith(prefix) and filename.endswith(".md"):
            files.append(filename)

    return {"files": sorted(files)}


# ── Evidence / screenshots (admin only) ──────────────────────────────────


@app.get("/api/jobs/{job_id}/screenshots")
def get_job_screenshots(
    job_id: int,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    evidences = (
        db.query(Evidence)
        .filter(Evidence.job_id == job_id, Evidence.evidence_type == "screenshot")
        .all()
    )
    return {
        "screenshots": [
            {
                "id": e.id,
                "storage_uri": e.storage_uri,
                "file_hash": e.file_hash,
                "created_ts": e.created_ts,
            }
            for e in evidences
        ]
    }


@app.get("/api/evidence/{evidence_id}/file")
def get_evidence_file(
    evidence_id: int,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    evidence = db.query(Evidence).filter(Evidence.id == evidence_id).first()
    if not evidence:
        raise HTTPException(status_code=404, detail="Evidence not found")
    if not evidence.storage_uri or not os.path.exists(evidence.storage_uri):
        raise HTTPException(status_code=404, detail="Evidence file not found on disk")

    return FileResponse(evidence.storage_uri, media_type="image/png")


# ── Job detail / resolve (admin only) ────────────────────────────────────


@app.get("/api/jobs/{job_id}/detail")
def get_job_detail(
    job_id: int,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    job = db.query(IngestJob).filter(IngestJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    evidences = db.query(Evidence).filter(Evidence.job_id == job_id).all()
    source = db.query(Source).filter(Source.id == job.source_id).first()

    return {
        "id": job.id,
        "url": job.url,
        "status": job.status.value,
        "strategy": job.strategy.value if job.strategy else None,
        "error_code": job.error_code,
        "started_ts": job.started_ts,
        "completed_ts": job.completed_ts,
        "max_depth": job.max_depth,
        "source_name": source.name if source else None,
        "evidences": [
            {
                "id": e.id,
                "type": e.evidence_type,
                "storage_uri": e.storage_uri,
                "file_hash": e.file_hash,
                "created_ts": e.created_ts,
            }
            for e in evidences
        ],
    }


@app.put("/api/jobs/{job_id}/resolve")
def resolve_job(
    job_id: int,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    job = db.query(IngestJob).filter(IngestJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (StatusEnum.FAILED, StatusEnum.CAPTCHA_DETECTED):
        raise HTTPException(status_code=400, detail="Only failed/blocked jobs can be resolved")

    job.status = StatusEnum.COMPLETED
    job.error_code = f"RESOLVED: {job.error_code or 'manual'}"
    db.commit()
    return {"message": f"Job {job_id} marked as resolved"}


# ── Sources / schedule management (admin only) ───────────────────────────


class ScheduleRequest(BaseModel):
    schedule: Optional[str] = None


@app.get("/api/sources")
def get_sources(
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    sources = db.query(Source).order_by(Source.id.desc()).all()
    return {
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "base_url": s.base_url,
                "permission_type": s.permission_type,
                "schedule": s.schedule_interval.value if s.schedule_interval else None,
                "last_scheduled_ts": s.last_scheduled_ts,
            }
            for s in sources
        ]
    }


@app.put("/api/sources/{source_id}/schedule")
def update_schedule(
    source_id: int,
    request: ScheduleRequest,
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    source = db.query(Source).filter(Source.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if request.schedule:
        try:
            source.schedule_interval = ScheduleEnum(request.schedule)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid schedule: {request.schedule}")
    else:
        source.schedule_interval = None

    db.commit()
    return {"message": f"Schedule updated for source '{source.name}'"}


# ── Analytics (admin only) ───────────────────────────────────────────────


@app.get("/api/analytics")
def get_analytics(
    db: Session = Depends(get_db),
    admin: UserInfo = Depends(get_current_admin),
):
    total_jobs = db.query(func.count(IngestJob.id)).scalar() or 0
    completed = db.query(func.count(IngestJob.id)).filter(IngestJob.status == StatusEnum.COMPLETED).scalar() or 0
    failed = db.query(func.count(IngestJob.id)).filter(IngestJob.status == StatusEnum.FAILED).scalar() or 0
    running = db.query(func.count(IngestJob.id)).filter(IngestJob.status == StatusEnum.RUNNING).scalar() or 0
    captcha = db.query(func.count(IngestJob.id)).filter(IngestJob.status == StatusEnum.CAPTCHA_DETECTED).scalar() or 0

    total_sources = db.query(func.count(Source.id)).scalar() or 0
    scheduled_sources = db.query(func.count(Source.id)).filter(Source.schedule_interval.isnot(None)).scalar() or 0

    total_evidences = db.query(func.count(Evidence.id)).scalar() or 0
    screenshots = db.query(func.count(Evidence.id)).filter(Evidence.evidence_type == "screenshot").scalar() or 0
    markdowns = db.query(func.count(Evidence.id)).filter(Evidence.evidence_type == "markdown").scalar() or 0

    # Strategy breakdown
    strategy_counts = {}
    for s in StrategyEnum:
        cnt = db.query(func.count(IngestJob.id)).filter(IngestJob.strategy == s).scalar() or 0
        if cnt > 0:
            strategy_counts[s.value] = cnt

    # Recent jobs (last 20)
    recent = db.query(IngestJob).order_by(IngestJob.started_ts.desc()).limit(20).all()
    recent_jobs = [
        {
            "id": j.id,
            "url": j.url,
            "status": j.status.value,
            "strategy": j.strategy.value if j.strategy else None,
            "started_ts": j.started_ts,
        }
        for j in recent
    ]

    return {
        "jobs": {"total": total_jobs, "completed": completed, "failed": failed, "running": running, "captcha": captcha},
        "sources": {"total": total_sources, "scheduled": scheduled_sources},
        "evidences": {"total": total_evidences, "screenshots": screenshots, "markdowns": markdowns},
        "strategies": strategy_counts,
        "recent_jobs": recent_jobs,
    }


# ── Public endpoints ─────────────────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str
    k: int = 3


@app.post("/api/search")
def search_api(request: SearchRequest):
    try:
        result_payload = query_rag(request.query, k=request.k)
        return result_payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/files/{filename}")
def get_file_content(filename: str):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    data_dir = os.getenv("DATA_DIR", "data")
    file_path = os.path.join(data_dir, filename)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    return {"content": content}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
