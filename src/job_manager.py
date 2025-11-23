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
    LinkStatus,
    ProductAction,
    ProductUpdateRequest,
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

    async def update_product_action(
        self,
        job_id: str,
        request: ProductUpdateRequest,
    ) -> dict:
        """
        Update a product's status based on user action.

        Args:
            job_id: Job ID
            request: Product update request

        Returns:
            Result of the update
        """
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        config = job.config

        if config.dry_run:
            return {
                "success": False,
                "error": "Cannot update products in dry-run mode",
                "action": request.action.value,
            }

        shopify_client = ShopifyClient(config.shop, config.token, config.api_version)

        try:
            if request.action == ProductAction.DRAFT:
                await shopify_client.update_product_status(request.product_id, "draft")
                action_taken = ActionType.DRAFTED
                job.stats.drafted_count += 1
            elif request.action == ProductAction.ARCHIVE:
                await shopify_client.update_product_status(request.product_id, "archived")
                action_taken = ActionType.ARCHIVED
                job.stats.archived_count += 1
            elif request.action == ProductAction.IGNORE:
                action_taken = ActionType.IGNORED
                job.stats.ignored_count += 1
            else:
                return {"success": False, "error": f"Unknown action: {request.action}"}

            # Update results for this product
            for result in job.results:
                if result.product_id == request.product_id:
                    result.action = action_taken

            return {
                "success": True,
                "action": action_taken.value,
                "product_id": request.product_id,
            }

        except Exception as e:
            logger.error(f"Failed to update product {request.product_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "action": request.action.value,
            }

        finally:
            await shopify_client.close()

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
            max_redirects=config.max_redirects,
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
                products_to_check = await self._fetch_products_by_collections(
                    shopify_client, config, job.checkpoint, seen_ids
                )
            else:
                products_to_check = await self._fetch_all_products(
                    shopify_client, config, job.checkpoint, seen_ids
                )

            # Apply date filter if specified
            if config.updated_after:
                products_to_check = [
                    p for p in products_to_check
                    if datetime.fromisoformat(p.get("updated_at", "").replace("Z", "+00:00"))
                    > config.updated_after
                ]

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

                # Process each product
                batch_results = []
                for product in product_batch:
                    results = await self._process_product(
                        product, config, shopify_client, link_checker, job, seen_ids
                    )
                    batch_results.extend(results)

                # Yield progress after each batch
                yield {
                    "status": job.status.value,
                    "stats": job.stats.model_dump(),
                    "results": [r.model_dump() for r in batch_results],
                }

            # Job completed successfully
            job.status = JobStatus.COMPLETED
            job.finished_at = datetime.utcnow()

            logger.info(
                f"Job {job_id} completed: {job.stats.processed} processed, "
                f"{job.stats.drafted_count} drafted, {job.stats.archived_count} archived"
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

    async def _process_product(
        self,
        product: dict,
        config: JobConfig,
        shopify_client: ShopifyClient,
        link_checker: LinkChecker,
        job: Job,
        seen_ids: set[int],
    ) -> list[CheckResult]:
        """Process a single product and return results."""
        product_id = product["id"]
        product_title = product.get("title", "")
        product_status = product.get("status", "")
        product_handle = product.get("handle", "")

        # Get product image if available
        product_image = None
        images = product.get("images", [])
        if images:
            product_image = images[0].get("src", "")

        # Skip if already seen
        if product_id in seen_ids:
            return []

        seen_ids.add(product_id)
        job.checkpoint.seen_ids.append(product_id)

        results = []

        # Fetch metafield
        metafield_value = await self._get_metafield_value(
            shopify_client, product_id, config.namespace, config.key
        )

        if not metafield_value:
            # No metafield found
            result = CheckResult(
                product_id=product_id,
                product_title=product_title,
                product_status=product_status,
                product_handle=product_handle,
                product_image=product_image,
                metafield=f"{config.namespace}.{config.key}",
                original_url="",
                final_url=None,
                link_status=LinkStatus.NO_URL,
                is_broken=False,
                action=ActionType.NO_URLS,
            )
            job.results.append(result)
            job.stats.processed += 1
            job.stats.no_url_count += 1
            return [result]

        # Extract URLs
        urls = extract_urls(metafield_value)

        if not urls:
            result = CheckResult(
                product_id=product_id,
                product_title=product_title,
                product_status=product_status,
                product_handle=product_handle,
                product_image=product_image,
                metafield=f"{config.namespace}.{config.key}",
                original_url="",
                final_url=None,
                link_status=LinkStatus.NO_URL,
                is_broken=False,
                action=ActionType.NO_URLS,
            )
            job.results.append(result)
            job.stats.processed += 1
            job.stats.no_url_count += 1
            return [result]

        # Check all URLs
        check_results = await link_checker.check_urls(urls)

        # Determine if product has broken links
        has_broken_links = any(r.is_broken for r in check_results)

        # Determine action
        action = ActionType.NO_ACTION
        if has_broken_links:
            job.stats.broken_url_count += 1

            if product_status == "draft":
                action = ActionType.ALREADY_DRAFT
            elif product_status == "archived":
                action = ActionType.ALREADY_ARCHIVED
            elif config.dry_run:
                if config.broken_action == ProductAction.ARCHIVE:
                    action = ActionType.WOULD_ARCHIVE
                else:
                    action = ActionType.WOULD_DRAFT
            elif config.auto_action:
                # Auto-apply action
                try:
                    if config.broken_action == ProductAction.ARCHIVE:
                        await shopify_client.update_product_status(product_id, "archived")
                        action = ActionType.ARCHIVED
                        job.stats.archived_count += 1
                    elif config.broken_action == ProductAction.DRAFT:
                        await shopify_client.update_product_status(product_id, "draft")
                        action = ActionType.DRAFTED
                        job.stats.drafted_count += 1
                    else:
                        action = ActionType.IGNORED
                        job.stats.ignored_count += 1
                except Exception as e:
                    logger.error(f"Failed to update product {product_id}: {e}")
                    action = ActionType.ERROR
                    job.stats.errors_count += 1
        else:
            # Update OK stats
            for r in check_results:
                if r.link_status == LinkStatus.OK:
                    job.stats.ok_url_count += 1
                elif r.link_status == LinkStatus.REDIRECTED_OK:
                    job.stats.redirected_ok_count += 1

        # Create results for each URL
        for check_result in check_results:
            result = CheckResult(
                product_id=product_id,
                product_title=product_title,
                product_status=product_status,
                product_handle=product_handle,
                product_image=product_image,
                metafield=f"{config.namespace}.{config.key}",
                original_url=check_result.original_url,
                final_url=check_result.final_url,
                http_status=check_result.http_status,
                link_status=check_result.link_status,
                is_broken=check_result.is_broken,
                was_redirected=check_result.was_redirected,
                redirect_count=check_result.redirect_count,
                error=check_result.error,
                action=action,
            )
            job.results.append(result)
            results.append(result)

        job.stats.processed += 1

        # Update checkpoint periodically
        if job.stats.processed % 100 == 0:
            job.checkpoint.processed_count = job.stats.processed

        return results

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

            while True:
                collects, next_page_info = await client.get_collects(
                    collection_id=collection_id,
                    limit=config.batch_size,
                    page_info=page_info,
                )

                product_ids = [c["product_id"] for c in collects]
                all_product_ids.update(product_ids)

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
