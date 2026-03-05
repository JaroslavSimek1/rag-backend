import os
from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models import init_db, Source, IngestJob, StatusEnum, Evidence
from ingestion import ingest_url
from rag import query_rag, delete_job_vectors

app = FastAPI(title="Web Data Ingestion API")

# Setup CORS for the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Dependency to get DB session
def get_db():
    db = init_db()
    try:
        yield db
    finally:
        db.close()


class IngestRequest(BaseModel):
    url: str
    source_name: str = "DefaultSource"
    deep_crawl: bool = False
    max_depth: int = 1


class JobResponse(BaseModel):
    message: str
    source_id: int
    status: str = "started"  # started, skipped, updated


@app.post("/api/ingest", response_model=JobResponse)
def trigger_ingestion(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Endpoint to trigger an ingestion job for a given URL.
    The actual scraping runs in the background.
    """
    # Find or create source
    source = db.query(Source).filter(Source.name == request.source_name).first()
    if not source:
        source = Source(name=request.source_name, base_url=request.url)
        db.add(source)
        db.commit()
        db.refresh(source)

    # Smart Ingest: Check if we already have this URL with sufficient depth
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

    # We trigger the ingestion in the background so the UI doesn't hang
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
def get_jobs(limit: int = 10, db: Session = Depends(get_db)):
    """
    Endpoint to fetch the latest ingestion jobs and their statuses.
    """
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


class SearchRequest(BaseModel):
    query: str
    k: int = 3


@app.post("/api/search")
def search_api(request: SearchRequest):
    """
    Endpoint to perform RAG search on the ingested knowledge base, synthesized by local Ollama.
    """
    try:
        # Call query_rag which retrieves chunks and synthesizes via Ollama
        result_payload = query_rag(request.query, k=request.k)

        return result_payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: int, db: Session = Depends(get_db)):
    """
    Deletes a job from DB, removes its files and its vectors from Qdrant.
    """
    job = db.query(IngestJob).filter(IngestJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # 1. Delete vectors from Qdrant
    try:
        delete_job_vectors(job_id)
    except Exception as e:
        print(f"Warning: Failed to delete vectors for job {job_id}: {e}")

    # 2. Delete Evidence and Files
    evidences = db.query(Evidence).filter(Evidence.job_id == job_id).all()
    for ev in evidences:
        # In a real app we'd delete the files from disk here too
        # For PoC, the filenames are stored in DB, we'll try to remove them if they exist
        for path_attr in ["html_path", "screenshot_path"]:
            path = getattr(ev, path_attr)
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
        db.delete(ev)

    # 3. Delete Job itself
    db.delete(job)
    db.commit()

    return {"message": f"Job {job_id} deleted successfully"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
