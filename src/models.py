"""Data models for the Shopify link checker."""

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProductStatus(str, Enum):
    """Shopify product status."""

    ACTIVE = "active"
    DRAFT = "draft"
    ARCHIVED = "archived"
    ANY = "any"


class ActionType(str, Enum):
    """Action taken on a product."""

    NO_ACTION = "no_action"
    DRAFTED = "drafted"
    ALREADY_DRAFT = "already_draft"
    WOULD_DRAFT = "would_draft"  # dry-run
    ERROR = "error"
    NO_URLS = "no_urls"


class CheckResult(BaseModel):
    """Result of checking a single URL."""

    product_id: int
    product_title: str
    product_status: str
    metafield: str
    url: str
    http_status: Optional[int] = None
    is_broken: bool
    error: Optional[str] = None
    action: ActionType
    checked_at: datetime = Field(default_factory=datetime.utcnow)

    def to_csv_row(self) -> list[str]:
        """Convert to CSV row."""
        return [
            str(self.product_id),
            self.product_title,
            self.product_status,
            self.metafield,
            self.url,
            str(self.http_status) if self.http_status else "",
            str(self.is_broken),
            self.error or "",
            self.action.value,
            self.checked_at.isoformat(),
        ]

    def to_jsonl(self) -> str:
        """Convert to JSONL string."""
        return self.model_dump_json()


class CollectsState(BaseModel):
    """State for collection-based pagination."""

    collection_id: int
    page_info: Optional[str] = None
    product_ids: list[int] = Field(default_factory=list)


class Checkpoint(BaseModel):
    """Checkpoint for resuming a job."""

    scope_hash: str
    page_info: Optional[str] = None
    collects_state: dict[int, CollectsState] = Field(default_factory=dict)
    seen_ids: list[int] = Field(default_factory=list)
    processed_count: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    @staticmethod
    def compute_scope_hash(
        shop: str,
        status: ProductStatus,
        namespace: str,
        key: str,
        collection_ids: Optional[list[int]],
    ) -> str:
        """Compute hash of job scope for resume validation."""
        scope_data = {
            "shop": shop,
            "status": status.value,
            "namespace": namespace,
            "key": key,
            "collection_ids": sorted(collection_ids) if collection_ids else None,
        }
        scope_str = json.dumps(scope_data, sort_keys=True)
        return hashlib.sha256(scope_str.encode()).hexdigest()


class JobConfig(BaseModel):
    """Configuration for a link checking job."""

    shop: str
    token: str
    namespace: str
    key: str
    status: ProductStatus = ProductStatus.ACTIVE
    collection_ids: Optional[list[int]] = None
    batch_size: int = Field(default=250, ge=1, le=250)
    concurrency: int = Field(default=20, ge=1, le=100)
    timeout_ms: int = Field(default=8000, ge=1000, le=60000)
    follow_redirects: bool = True
    dry_run: bool = False
    resume_token: Optional[str] = None
    api_version: str = "2024-10"

    def get_scope_hash(self) -> str:
        """Get scope hash for this config."""
        return Checkpoint.compute_scope_hash(
            self.shop, self.status, self.namespace, self.key, self.collection_ids
        )


class JobStatus(str, Enum):
    """Job execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobStats(BaseModel):
    """Statistics for a job."""

    total_products: int = 0
    processed: int = 0
    drafted_count: int = 0
    broken_url_count: int = 0
    errors_count: int = 0
    batch_index: int = 0
    total_batches: int = 0


class Job(BaseModel):
    """A link checking job."""

    id: str
    config: JobConfig
    status: JobStatus = JobStatus.PENDING
    stats: JobStats = Field(default_factory=JobStats)
    results: list[CheckResult] = Field(default_factory=list)
    checkpoint: Optional[Checkpoint] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None

    def get_resume_token(self) -> Optional[str]:
        """Get base64-encoded resume token."""
        if not self.checkpoint:
            return None
        import base64

        checkpoint_json = self.checkpoint.model_dump_json()
        return base64.b64encode(checkpoint_json.encode()).decode()

    @staticmethod
    def decode_resume_token(token: str) -> Checkpoint:
        """Decode base64 resume token."""
        import base64

        checkpoint_json = base64.b64decode(token.encode()).decode()
        return Checkpoint.model_validate_json(checkpoint_json)
