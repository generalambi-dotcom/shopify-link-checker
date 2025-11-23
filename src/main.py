"""FastAPI application for Shopify link checker."""

import csv
import io
import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from .job_manager import JobManager
from .models import (
    CheckResult,
    JobConfig,
    LinkStatus,
    ProductAction,
    ProductStatus,
    ProductUpdateRequest,
)

# Configure logging
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Shopify Link Checker",
    description="Production-ready tool for validating product metafield URLs",
    version="2.0.0",
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
    max_redirects: int = Field(5, ge=1, le=20, description="Max redirects to follow")
    follow_redirects: bool = Field(True, description="Follow HTTP redirects")
    dry_run: bool = Field(False, description="Dry run mode (don't actually modify products)")
    resume_token: Optional[str] = Field(None, description="Resume token for continuing a job")
    api_version: str = Field("2024-10", description="Shopify API version")
    updated_after: Optional[str] = Field(None, description="Only check products updated after this ISO date")
    broken_action: ProductAction = Field(ProductAction.DRAFT, description="Action for broken links")
    auto_action: bool = Field(False, description="Auto-apply action to products with broken links")


class RunJobResponse(BaseModel):
    """Response model for starting a job."""

    job_id: str
    message: str


class ProductActionRequest(BaseModel):
    """Request to update a product."""

    product_id: int
    action: ProductAction


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
        # Parse updated_after if provided
        updated_after = None
        if req.updated_after:
            try:
                updated_after = datetime.fromisoformat(req.updated_after.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date format for updated_after")

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
            max_redirects=req.max_redirects,
            follow_redirects=req.follow_redirects,
            dry_run=req.dry_run,
            resume_token=req.resume_token,
            api_version=req.api_version,
            updated_after=updated_after,
            broken_action=req.broken_action,
            auto_action=req.auto_action,
        )

        # Create job
        job_id = job_manager.create_job(config)

        logger.info(f"Created job {job_id} for shop {req.shop}")

        return RunJobResponse(
            job_id=job_id,
            message="Job created successfully. Use /jobs/{job_id}/stream to start and monitor.",
        )

    except HTTPException:
        raise
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
        "dry_run": job.config.dry_run,
    }


@app.get("/jobs/{job_id}/results")
async def get_job_results(
    job_id: str,
    broken_only: bool = Query(False, description="Only return broken links"),
    status_filter: Optional[str] = Query(None, description="Comma-separated link statuses"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(50, ge=1, le=500, description="Results per page"),
):
    """
    Get paginated job results with optional filtering.

    Returns:
        Paginated list of check results
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Parse status filter
    status_list = None
    if status_filter:
        try:
            status_list = [LinkStatus(s.strip()) for s in status_filter.split(",")]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid status filter: {e}")

    # Get filtered results
    results = job.get_filtered_results(broken_only=broken_only, status_filter=status_list)

    # Paginate
    total = len(results)
    start = (page - 1) * per_page
    end = start + per_page
    paginated_results = results[start:end]

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page,
        "results": [r.model_dump() for r in paginated_results],
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


@app.post("/jobs/{job_id}/products/{product_id}/action")
async def update_product_action(job_id: str, product_id: int, req: ProductActionRequest):
    """
    Update a product's status (draft, archive, or ignore).

    Returns:
        Result of the action
    """
    try:
        request = ProductUpdateRequest(product_id=product_id, action=req.action)
        result = await job_manager.update_product_action(job_id, request)
        return result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to update product: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs/{job_id}/csv")
async def download_csv(
    job_id: str,
    broken_only: bool = Query(False, description="Only export broken links"),
):
    """
    Download job results as CSV.

    Returns:
        CSV file with all checked URLs and results
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get filtered results
    results = job.get_filtered_results(broken_only=broken_only)

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)

    # Write header
    writer.writerow(CheckResult.csv_headers())

    # Write results
    for result in results:
        writer.writerow(result.to_csv_row())

    # Return as download
    output.seek(0)
    filename = f"link_check_{job_id}{'_broken' if broken_only else ''}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/jobs/{job_id}/jsonl")
async def download_jsonl(
    job_id: str,
    broken_only: bool = Query(False, description="Only export broken links"),
):
    """
    Download job results as JSONL.

    Returns:
        JSONL file with all checked URLs and results
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get filtered results
    results = job.get_filtered_results(broken_only=broken_only)

    # Create JSONL in memory
    output = io.StringIO()

    for result in results:
        output.write(result.to_jsonl() + "\n")

    # Return as download
    output.seek(0)
    filename = f"link_check_{job_id}{'_broken' if broken_only else ''}.jsonl"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
