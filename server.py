"""
Business Tracker — FastAPI Backend Server
==========================================
Provides REST API endpoints for scraping Google Maps business data.

Endpoints:
  POST /api/scrape          — Start a new scrape job
  GET  /api/status/{job_id} — Poll job progress & partial results
  GET  /api/download/{job_id} — Download results as CSV
"""

import csv
import io
import uuid
import sys
import asyncio
import traceback
from datetime import datetime
from enum import Enum
from typing import Optional

# Playwright requires the ProactorEventLoop on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# App & CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Business Tracker API",
    description="Scrape Google Maps business listings via Playwright",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScrapeRequest(BaseModel):
    """Payload for POST /api/scrape."""
    business_type: str = Field(..., min_length=1, examples=["restaurants"])
    location: str = Field(..., min_length=1, examples=["New York"])


class BusinessResult(BaseModel):
    """A single scraped business listing."""
    name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[str] = None
    reviews: Optional[str] = None
    category: Optional[str] = None
    profile_link: Optional[str] = None


class JobInfo(BaseModel):
    """Full state of a scrape job."""
    job_id: str
    status: JobStatus
    business_type: str
    location: str
    progress: int = 0          # Number of results collected so far
    total: Optional[int] = None  # Estimated total (if known)
    results: list[BusinessResult] = []
    error: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

jobs: dict[str, JobInfo] = {}

# ---------------------------------------------------------------------------
# Background scraping task
# ---------------------------------------------------------------------------


def _run_scrape_job(job_id: str) -> None:
    """Execute the scraping logic in a background thread for *job_id*."""
    job = jobs[job_id]
    job.status = JobStatus.RUNNING

    try:
        # Import scraper lazily so the server can start even if scraper.py
        # isn't ready yet (useful during development).
        from scraper import scrape_google_maps  # type: ignore

        # Progress callback — updates job state in real-time
        def on_progress(info: dict):
            phase = info.get("phase", "")
            if phase == "navigating":
                job.total = 0
            elif phase == "searching":
                pass
            elif phase == "scrolling":
                job.total = info.get("results_loaded", 0)
            elif phase == "extracting":
                job.progress = info.get("current", 0)
                job.total = info.get("total", job.total)

        # Run the async scraper in a dedicated ProactorEventLoop in this thread
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        
        asyncio.set_event_loop(loop)
        
        data = loop.run_until_complete(
            scrape_google_maps(
                job.business_type,
                job.location,
                on_progress=on_progress,
            )
        )
        loop.close()

        for item in data:
            job.results.append(BusinessResult(**item) if isinstance(item, dict) else item)
        job.progress = len(job.results)

        job.status = JobStatus.COMPLETED
        job.completed_at = datetime.utcnow().isoformat()

    except Exception as exc:
        job.status = JobStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        job.completed_at = datetime.utcnow().isoformat()

# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@app.post("/api/scrape", response_model=dict)
async def start_scrape(req: ScrapeRequest, bg_tasks: BackgroundTasks):
    """
    Start a new scraping job.

    Returns the generated ``job_id`` which can be used to poll progress or
    download results.
    """
    job_id = uuid.uuid4().hex[:12]

    job = JobInfo(
        job_id=job_id,
        status=JobStatus.PENDING,
        business_type=req.business_type,
        location=req.location,
        created_at=datetime.utcnow().isoformat(),
    )
    jobs[job_id] = job

    # Fire-and-forget the scrape task in a background thread
    bg_tasks.add_task(_run_scrape_job, job_id)

    return {"job_id": job_id, "message": "Scrape job started"}


@app.get("/api/status/{job_id}", response_model=JobInfo)
async def get_status(job_id: str):
    """
    Poll the current status of a scrape job.

    Returns progress count, partial results collected so far, and the
    overall job status.
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/download/{job_id}")
async def download_csv(job_id: str):
    """
    Download the scraped results as a CSV file.

    Only available once the job status is ``completed`` (partial downloads
    are also allowed while the job is ``running``).
    """
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job.results:
        raise HTTPException(status_code=400, detail="No results available yet")

    # Build CSV in-memory
    output = io.StringIO()
    fieldnames = ["name", "phone", "address", "website", "rating", "reviews", "category", "profile_link"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for biz in job.results:
        writer.writerow(biz.model_dump())

    output.seek(0)

    filename = f"{job.business_type}_{job.location}_{job_id}.csv".replace(" ", "_")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    """Simple health-check endpoint."""
    return {"status": "ok", "jobs_count": len(jobs)}


# ---------------------------------------------------------------------------
# Mount frontend static files
# ---------------------------------------------------------------------------

import os
_frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/", StaticFiles(directory=_frontend_dir, html=True), name="frontend")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
