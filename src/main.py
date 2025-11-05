"""FastAPI application for Shopify link checker."""

import csv
import io
import json
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .job_manager import JobManager
from .models import JobConfig, ProductStatus

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Shopify Link Checker",
    description="Production-ready tool for validating product metafield URLs",
    version="1.0.0",
)

templates = Jinja2Templates(directory="templates")
job_manager = JobManager()


class RunJobRequest(BaseModel):
    """Request model for running a job."""

    shop: str = Field(..., description="Shop domain (e.g., my-shop.myshopify.com)")
    token: str = Field(..., description="Admin API access token")
    namespace: str = Field(..., description="Metafield namespace")
    key: str = Field(..., description="Metafield key")
    status: ProductStatus = Field(ProductStatus.ACTIVE, description="Product status filter")
    collection_ids: Optional[list[int]] = Field(None, description="Collection IDs to filter by")
    batch_size: int = Field(250, ge=1, le=250, description="Batch size (max 250)")
    concurrency: int = Field(20, ge=1, le=100, description="Concurrent link checks")
    timeout_ms: int = Field(8000, ge=1000, le=60000, description="Timeout in milliseconds")
    follow_redirects: bool = Field(True, description="Follow HTTP redirects")
    dry_run: bool = Field(False, description="Dry run mode (don't actually draft products)")
    resume_token: Optional[str] = Field(None, description="Resume token for continuing a job")
    api_version: str = Field("2024-10", description="Shopify API version")


class RunJobResponse(BaseModel):
    """Response model for starting a job."""

    job_id: str
    message: str


class JobStatusResponse(BaseModel):
    """Response model for job status."""

    job_id: str
    status: str
    stats: dict
    resume_token: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the web UI."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/run", response_model=RunJobResponse)
async def run_job(req: RunJobRequest):
    """
    Start a new link checking job.

    Returns:
        Job ID and status message
    """
    try:
        # Create job config
        config = JobConfig(
            shop=req.shop,
            token=req.token,
            namespace=req.namespace,
            key=req.key,
            status=req.status,
            collection_ids=req.collection_ids,
            batch_size=req.batch_size,
            concurrency=req.concurrency,
            timeout_ms=req.timeout_ms,
            follow_redirects=req.follow_redirects,
            dry_run=req.dry_run,
            resume_token=req.resume_token,
            api_version=req.api_version,
        )

        # Create job
        job_id = job_manager.create_job(config)

        logger.info(f"Created job {job_id} for shop {req.shop}")

        return RunJobResponse(
            job_id=job_id,
            message="Job created successfully. Use /jobs/{job_id} to monitor progress.",
        )

    except Exception as e:
        logger.error(f"Failed to create job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """
    Get job status and progress.

    Returns:
        Job status, stats, and partial results
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job.id,
        "status": job.status.value,
        "stats": job.stats.model_dump(),
        "results_count": len(job.results),
        "resume_token": job.get_resume_token(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error": job.error,
    }


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    """
    Stream job progress using Server-Sent Events.

    Returns:
        SSE stream with job updates
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_stream():
        """Generate SSE events."""
        try:
            async for update in job_manager.run_job(job_id):
                # Send SSE event
                yield f"data: {json.dumps(update)}\n\n"

        except Exception as e:
            logger.error(f"Error streaming job {job_id}: {e}", exc_info=True)
            error_data = {"status": "failed", "error": str(e)}
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/jobs/{job_id}/csv")
async def download_csv(job_id: str):
    """
    Download job results as CSV.

    Returns:
        CSV file with all checked URLs and results
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow([
        "product_id",
        "product_title",
        "product_status",
        "metafield",
        "url",
        "http_status",
        "is_broken",
        "error",
        "action",
        "checked_at",
    ])

    # Write results
    for result in job.results:
        writer.writerow(result.to_csv_row())

    # Return as download
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=link_check_{job_id}.csv"},
    )


@app.get("/jobs/{job_id}/jsonl")
async def download_jsonl(job_id: str):
    """
    Download job results as JSONL.

    Returns:
        JSONL file with all checked URLs and results
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Create JSONL in memory
    output = io.StringIO()

    for result in job.results:
        output.write(result.to_jsonl() + "\n")

    # Return as download
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename=link_check_{job_id}.jsonl"},
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
