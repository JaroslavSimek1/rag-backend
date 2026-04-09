import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import get_db, Source, IngestJob, StatusEnum, Evidence, ScheduleEnum
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
        source = Source(name=request.source_name, base_url=request.url)
        db.add(source)
        db.commit()
        db.refresh(source)

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
        ingest_url, request.url, source.id, request.deep_crawl, request.max_depth
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
        evidence = db.query(Evidence).filter(Evidence.job_id == j.id).first()
        result.append(
            {
                "id": j.id,
                "url": j.url,
                "status": j.status.value,
                "error_code": j.error_code,
                "started_ts": j.started_ts,
                "has_evidence": evidence is not None,
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
