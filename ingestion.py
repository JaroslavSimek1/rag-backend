import os
import re
import base64
import hashlib
import tempfile
import requests as http_requests
from firecrawl import FirecrawlApp
from models import init_db, Source, IngestJob, StrategyEnum, StatusEnum, Evidence
from datetime import datetime, timezone

FIRECRAWL_URL = os.getenv("FIRECRAWL_URL", "http://localhost:3002")
FIRECRAWL_KEY = os.getenv("FIRECRAWL_KEY", "fc-local-key")

# Minimum characters of markdown to consider it "sufficient" text output
MIN_TEXT_LENGTH = 100

app = FirecrawlApp(api_url=FIRECRAWL_URL, api_key=FIRECRAWL_KEY)


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _save_screenshot(screenshot_b64: str, job_id: int, idx: int, data_dir: str) -> tuple[str, str]:
    """Save a base64 screenshot to disk. Returns (file_path, sha256_hash)."""
    evidence_dir = os.path.join(data_dir, "evidence")
    os.makedirs(evidence_dir, exist_ok=True)

    img_bytes = base64.b64decode(screenshot_b64)
    file_hash = _compute_sha256(img_bytes)
    file_path = os.path.join(evidence_dir, f"job_{job_id}_{idx}.png")

    with open(file_path, "wb") as f:
        f.write(img_bytes)

    return file_path, file_hash


def _detect_strategy(doc) -> StrategyEnum:
    """Detect which strategy Firecrawl effectively used based on what it returned."""
    has_markdown = bool(doc.markdown and len(doc.markdown.strip()) >= MIN_TEXT_LENGTH)
    has_html = bool(getattr(doc, "html", None))
    has_screenshot = bool(getattr(doc, "screenshot", None))

    if has_markdown and has_html:
        return StrategyEnum.HTML
    if has_markdown and not has_html:
        return StrategyEnum.RENDER
    if has_screenshot and not has_markdown:
        return StrategyEnum.SCREENSHOT
    return StrategyEnum.HTML


IMAGE_MD_PATTERN = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def _ocr_images_in_markdown(markdown: str) -> str:
    """Download images referenced in markdown and replace them with OCR-extracted text."""
    from ocr import ocr_from_file

    def _replace_image(match):
        alt_text = match.group(1)
        img_url = match.group(2)

        # Skip data URIs (base64) — those are handled separately
        if img_url.startswith("data:"):
            return match.group(0)

        # Skip SVGs and tiny icons
        if any(img_url.lower().endswith(ext) for ext in (".svg", ".ico", ".gif")):
            return alt_text or ""

        try:
            resp = http_requests.get(img_url, timeout=10)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "image" not in content_type:
                return alt_text or ""

            suffix = ".png"
            if "jpeg" in content_type or "jpg" in content_type:
                suffix = ".jpg"
            elif "webp" in content_type:
                suffix = ".webp"

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name

            try:
                ocr_text = ocr_from_file(tmp_path)
                if ocr_text.strip():
                    return ocr_text.strip()
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            print(f"[OCR-img] Failed for {img_url}: {e}")

        return alt_text or ""

    return IMAGE_MD_PATTERN.sub(_replace_image, markdown)


def _get_doc_url(doc, fallback_url: str) -> str:
    if isinstance(doc.metadata, dict):
        return doc.metadata.get("url", fallback_url)
    return doc.metadata.url if getattr(doc.metadata, "url", None) else fallback_url


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

        # Detect strategy from the first document
        if scraped_docs:
            job.strategy = _detect_strategy(scraped_docs[0])
            print(f"[Strategy detected: {job.strategy.value}]")

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
                data_dir = os.getenv("DATA_DIR", "data")
                file_path, file_hash = _save_screenshot(screenshot_b64, job.id, 0, data_dir)
                evidence = Evidence(
                    job_id=job.id,
                    evidence_type="screenshot",
                    storage_uri=file_path,
                    file_hash=file_hash,
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
            doc_url = _get_doc_url(doc, url)
            safe_url_str = (
                str(doc_url)
                .replace("https://", "")
                .replace("http://", "")
                .replace("/", "_")
            )

            # Save screenshot as evidence artifact + compute SHA-256
            screenshot_b64 = getattr(doc, "screenshot", None)
            if screenshot_b64:
                file_path, file_hash = _save_screenshot(screenshot_b64, job.id, idx, data_dir)
                evidence = Evidence(
                    job_id=job.id,
                    evidence_type="screenshot",
                    storage_uri=file_path,
                    file_hash=file_hash,
                    created_ts=datetime.now(timezone.utc),
                )
                db.add(evidence)
                print(f"[Evidence] Screenshot saved: {file_path} (SHA-256: {file_hash[:16]}...)")

            # Determine text content — use markdown, or fall back to OCR
            text_content = doc.markdown or ""

            if len(text_content.strip()) < MIN_TEXT_LENGTH and screenshot_b64:
                print(f"[OCR Fallback] Markdown insufficient ({len(text_content)} chars), running OCR...")
                job.strategy = StrategyEnum.SCREENSHOT
                try:
                    from ocr import ocr_from_base64
                    ocr_text = ocr_from_base64(screenshot_b64)
                    if ocr_text.strip():
                        text_content = ocr_text
                        print(f"[OCR] Extracted {len(ocr_text)} chars from screenshot")
                    else:
                        print("[OCR] No text extracted from screenshot")
                except Exception as e:
                    print(f"[OCR Error] {e}")

            # OCR images embedded in markdown (![alt](url) → extracted text)
            if IMAGE_MD_PATTERN.search(text_content):
                img_count = len(IMAGE_MD_PATTERN.findall(text_content))
                print(f"[OCR-img] Processing {img_count} image(s) in markdown...")
                try:
                    text_content = _ocr_images_in_markdown(text_content)
                except Exception as e:
                    print(f"[OCR-img Error] {e}")

            if not text_content.strip():
                continue

            filename = f"{data_dir}/job_{job.id}_{idx}_{safe_url_str}.md"

            with open(filename, "w", encoding="utf-8") as f:
                f.write(text_content)

            # Hash the markdown file too
            md_hash = _compute_sha256(text_content.encode("utf-8"))
            evidence_md = Evidence(
                job_id=job.id,
                evidence_type="markdown",
                storage_uri=filename,
                file_hash=md_hash,
                created_ts=datetime.now(timezone.utc),
            )
            db.add(evidence_md)

            print(f"Routing {filename} to Vector Engine (Qdrant)...")
            index_markdown_file(filename, doc_url, job_id=job.id)

        print("Canonical Documents Extracted & Indexed.")
        db.commit()

    except Exception as e:
        print(f"[ERROR] Failed ingestion: {str(e)}")
        job.status = StatusEnum.FAILED
        job.error_code = str(e)
        job.completed_ts = datetime.now(timezone.utc)
        db.commit()
