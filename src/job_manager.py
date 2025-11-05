"""Job manager for orchestrating link checking jobs with resume support."""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import AsyncGenerator, Optional

from .link_checker import LinkChecker
from .models import (
    ActionType,
    CheckResult,
    Checkpoint,
    CollectsState,
    Job,
    JobConfig,
    JobStats,
    JobStatus,
)
from .shopify_client import ShopifyClient
from .utils import chunk_list, extract_urls

logger = logging.getLogger(__name__)


class JobManager:
    """
    Manages link checking jobs with batching, deduplication, and resume support.
    """

    def __init__(self):
        self.jobs: dict[str, Job] = {}

    def create_job(self, config: JobConfig) -> str:
        """Create a new job and return its ID."""
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, config=config)

        # If resuming, decode checkpoint
        if config.resume_token:
            try:
                checkpoint = Job.decode_resume_token(config.resume_token)
                # Validate scope hash
                if checkpoint.scope_hash == config.get_scope_hash():
                    job.checkpoint = checkpoint
                    logger.info(f"Resuming job from checkpoint with {len(checkpoint.seen_ids)} seen IDs")
                else:
                    logger.warning("Resume token scope mismatch, starting fresh job")
            except Exception as e:
                logger.error(f"Failed to decode resume token: {e}")

        self.jobs[job_id] = job
        return job_id

    def get_job(self, job_id: str) -> Optional[Job]:
        """Get job by ID."""
        return self.jobs.get(job_id)

    async def run_job(self, job_id: str) -> AsyncGenerator[dict, None]:
        """
        Run a link checking job and yield progress updates.

        Yields:
            Progress updates with stats and results
        """
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        job.status = JobStatus.RUNNING
        job.started_at = datetime.utcnow()

        config = job.config
        shopify_client = ShopifyClient(config.shop, config.token, config.api_version)
        link_checker = LinkChecker(
            timeout_ms=config.timeout_ms,
            follow_redirects=config.follow_redirects,
            concurrency=config.concurrency,
        )

        try:
            # Initialize checkpoint if not resuming
            if not job.checkpoint:
                job.checkpoint = Checkpoint(
                    scope_hash=config.get_scope_hash(),
                    seen_ids=[],
                    processed_count=0,
                )

            seen_ids = set(job.checkpoint.seen_ids)

            # Determine product fetch strategy
            if config.collection_ids:
                # Collection-based fetching
                products_to_check = await self._fetch_products_by_collections(
                    shopify_client,
                    config,
                    job.checkpoint,
                    seen_ids,
                )
            else:
                # All products fetching
                products_to_check = await self._fetch_all_products(
                    shopify_client,
                    config,
                    job.checkpoint,
                    seen_ids,
                )

            # Update total count
            job.stats.total_products = len(products_to_check)

            # Calculate total batches
            job.stats.total_batches = (
                len(products_to_check) + config.batch_size - 1
            ) // config.batch_size

            # Process products in batches
            for batch_idx, product_batch in enumerate(
                chunk_list(products_to_check, config.batch_size)
            ):
                job.stats.batch_index = batch_idx + 1

                logger.info(
                    f"Processing batch {job.stats.batch_index}/{job.stats.total_batches} "
                    f"({len(product_batch)} products)"
                )

                # Check each product in the batch
                for product in product_batch:
                    product_id = product["id"]
                    product_title = product.get("title", "")
                    product_status = product.get("status", "")

                    # Skip if already seen (shouldn't happen, but safety check)
                    if product_id in seen_ids:
                        continue

                    seen_ids.add(product_id)
                    job.checkpoint.seen_ids.append(product_id)

                    # Fetch metafield
                    metafield_value = await self._get_metafield_value(
                        shopify_client,
                        product_id,
                        config.namespace,
                        config.key,
                    )

                    if not metafield_value:
                        # No metafield found
                        result = CheckResult(
                            product_id=product_id,
                            product_title=product_title,
                            product_status=product_status,
                            metafield=f"{config.namespace}.{config.key}",
                            url="",
                            is_broken=False,
                            action=ActionType.NO_URLS,
                        )
                        job.results.append(result)
                        job.stats.processed += 1
                        continue

                    # Extract URLs
                    urls = extract_urls(metafield_value)

                    if not urls:
                        result = CheckResult(
                            product_id=product_id,
                            product_title=product_title,
                            product_status=product_status,
                            metafield=f"{config.namespace}.{config.key}",
                            url="",
                            is_broken=False,
                            action=ActionType.NO_URLS,
                        )
                        job.results.append(result)
                        job.stats.processed += 1
                        continue

                    # Check all URLs
                    check_results = await link_checker.check_urls(urls)

                    # Determine if product has broken links
                    has_broken_links = any(r.is_broken for r in check_results)

                    # Take action if needed
                    action = ActionType.NO_ACTION
                    if has_broken_links:
                        job.stats.broken_url_count += 1

                        if product_status == "draft":
                            action = ActionType.ALREADY_DRAFT
                        elif config.dry_run:
                            action = ActionType.WOULD_DRAFT
                        else:
                            # Draft the product
                            try:
                                await shopify_client.update_product_status(product_id, "draft")
                                action = ActionType.DRAFTED
                                job.stats.drafted_count += 1
                            except Exception as e:
                                logger.error(f"Failed to draft product {product_id}: {e}")
                                action = ActionType.ERROR
                                job.stats.errors_count += 1

                    # Create results for each URL
                    for check_result in check_results:
                        result = CheckResult(
                            product_id=product_id,
                            product_title=product_title,
                            product_status=product_status,
                            metafield=f"{config.namespace}.{config.key}",
                            url=check_result.url,
                            http_status=check_result.http_status,
                            is_broken=check_result.is_broken,
                            error=check_result.error,
                            action=action,
                        )
                        job.results.append(result)

                    job.stats.processed += 1

                    # Update checkpoint periodically (every 100 products)
                    if job.stats.processed % 100 == 0:
                        job.checkpoint.processed_count = job.stats.processed

                # Yield progress after each batch
                yield {
                    "status": job.status.value,
                    "stats": job.stats.model_dump(),
                    "results": [r.model_dump() for r in job.results[-len(product_batch) * 10 :]],
                }

            # Job completed successfully
            job.status = JobStatus.COMPLETED
            job.finished_at = datetime.utcnow()

            logger.info(
                f"Job {job_id} completed: {job.stats.processed} processed, "
                f"{job.stats.drafted_count} drafted"
            )

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.finished_at = datetime.utcnow()
            raise

        finally:
            await shopify_client.close()
            await link_checker.close()

            # Final yield
            yield {
                "status": job.status.value,
                "stats": job.stats.model_dump(),
                "resume_token": job.get_resume_token(),
            }

    async def _fetch_all_products(
        self,
        client: ShopifyClient,
        config: JobConfig,
        checkpoint: Checkpoint,
        seen_ids: set[int],
    ) -> list[dict]:
        """Fetch all products with cursor pagination."""
        products = []
        page_info = checkpoint.page_info

        while True:
            batch, next_page_info = await client.get_products(
                status=config.status,
                limit=config.batch_size,
                page_info=page_info,
            )

            # Filter out already-seen products
            new_products = [p for p in batch if p["id"] not in seen_ids]
            products.extend(new_products)

            # Update checkpoint
            checkpoint.page_info = next_page_info

            if not next_page_info:
                break

            page_info = next_page_info

        return products

    async def _fetch_products_by_collections(
        self,
        client: ShopifyClient,
        config: JobConfig,
        checkpoint: Checkpoint,
        seen_ids: set[int],
    ) -> list[dict]:
        """Fetch products by collection IDs with deduplication."""
        all_product_ids = set()

        for collection_id in config.collection_ids:
            # Get or create collects state
            if collection_id not in checkpoint.collects_state:
                checkpoint.collects_state[collection_id] = CollectsState(
                    collection_id=collection_id
                )

            collects_state = checkpoint.collects_state[collection_id]
            page_info = collects_state.page_info

            # Fetch collects (product IDs) for this collection
            while True:
                collects, next_page_info = await client.get_collects(
                    collection_id=collection_id,
                    limit=config.batch_size,
                    page_info=page_info,
                )

                # Extract product IDs
                product_ids = [c["product_id"] for c in collects]
                all_product_ids.update(product_ids)

                # Update state
                collects_state.page_info = next_page_info
                collects_state.product_ids.extend(product_ids)

                if not next_page_info:
                    break

                page_info = next_page_info

        # Remove already-seen IDs
        product_ids_to_fetch = [pid for pid in all_product_ids if pid not in seen_ids]

        # Hydrate product details in chunks
        products = []
        for id_chunk in chunk_list(product_ids_to_fetch, 250):
            batch = await client.get_products_by_ids(id_chunk)

            # Apply status filter
            if config.status != "any":
                batch = [p for p in batch if p.get("status") == config.status.value]

            products.extend(batch)

        return products

    async def _get_metafield_value(
        self,
        client: ShopifyClient,
        product_id: int,
        namespace: str,
        key: str,
    ) -> Optional[str]:
        """Get metafield value for a product."""
        try:
            metafields = await client.get_product_metafields(
                product_id=product_id,
                namespace=namespace,
                key=key,
            )

            if metafields:
                return metafields[0].get("value", "")

            return None

        except Exception as e:
            logger.error(f"Failed to fetch metafield for product {product_id}: {e}")
            return None
