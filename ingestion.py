import os
from firecrawl import FirecrawlApp
from models import init_db, Source, IngestJob, StrategyEnum, StatusEnum, Evidence
from datetime import datetime, timezone

FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002")
FIRECRAWL_KEY = os.getenv("FIRECRAWL_KEY", "fc-local-key")

app = FirecrawlApp(api_url=FIRECRAWL_URL, api_key=FIRECRAWL_KEY)


def ingest_url(url: str, source_id: int, deep_crawl: bool = False, max_depth: int = 2):
    db = init_db()

    print(f"Starting ingestion process for URL: {url}")

    job = IngestJob(
        source_id=source_id,
        url=url,
        strategy=StrategyEnum.HTML,
        status=StatusEnum.RUNNING,
        max_depth=max_depth,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        if deep_crawl:
            print(
                f"[Strategy: CRAWLER] Initiating deep crawl for {url} with depth {max_depth}..."
            )
            job.strategy = StrategyEnum.API
            crawl_job = app.crawl(
                url,
                limit=30,
                max_discovery_depth=max_depth,
                scrape_options={"formats": ["markdown", "html", "screenshot"]},
            )
            scraped_docs = crawl_job.data
        else:
            print(f"[Strategy: HTML] Attempting to scrape single page {url}...")
            scrape_result = app.scrape(
                url,
                formats=["markdown", "html", "screenshot"],
            )
            scraped_docs = [scrape_result]

        markdown_content = (
            (scraped_docs[0].markdown or "").lower() if scraped_docs else ""
        )
        if (
            "captcha" in markdown_content
            or "verify you are human" in markdown_content
            or "cloudflare" in markdown_content
        ):
            print(f"[CAPTCHA DETECTED] Creating Incident and collecting evidence...")
            job.status = StatusEnum.CAPTCHA_DETECTED
            job.error_code = "CAPTCHA"

            screenshot_b64 = scraped_docs[0].screenshot if scraped_docs else None
            if screenshot_b64:
                evidence = Evidence(
                    job_id=job.id,
                    evidence_type="screenshot",
                    storage_uri=f"s3://evidence-store/captcha_{job.id}.png",
                    file_hash="dummy_hash",
                    created_ts=datetime.now(timezone.utc),
                )
                db.add(evidence)
            db.commit()
            print("CAPTCHA handled. Incident queued.")
            return

        print(f"Scrape successful. Processing {len(scraped_docs)} document(s)...")
        job.status = StatusEnum.COMPLETED
        job.completed_ts = datetime.now(timezone.utc)

        from rag import index_markdown_file

        data_dir = os.getenv("DATA_DIR", "data")
        os.makedirs(data_dir, exist_ok=True)

        for idx, doc in enumerate(scraped_docs):
            if not doc.markdown:
                continue

            safe_url = (
                doc.metadata.get("url", url)
                if isinstance(doc.metadata, dict)
                else (doc.metadata.url if getattr(doc.metadata, "url", None) else url)
            )
            safe_url_str = (
                str(safe_url)
                .replace("https://", "")
                .replace("http://", "")
                .replace("/", "_")
            )
            filename = f"{data_dir}/job_{job.id}_{idx}_{safe_url_str}.md"

            with open(filename, "w", encoding="utf-8") as f:
                f.write(doc.markdown)

            print(f"Routing {filename} to Vector Engine (Qdrant)...")
            index_markdown_file(filename, safe_url, job_id=job.id)

        print("Canonical Documents Extracted & Indexed.")
        db.commit()

    except Exception as e:
        print(f"[ERROR] Failed ingestion: {str(e)}")
        job.status = StatusEnum.FAILED
        job.error_code = str(e)
        job.completed_ts = datetime.now(timezone.utc)
        db.commit()
