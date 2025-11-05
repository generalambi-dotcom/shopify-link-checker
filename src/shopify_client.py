"""Shopify API client with cursor pagination and rate limiting."""

import asyncio
import logging
from typing import Any, Optional

import httpx

from .models import ProductStatus
from .utils import (
    exponential_backoff_with_jitter,
    parse_link_header,
    parse_rate_limit_header,
    should_throttle,
)

logger = logging.getLogger(__name__)


class ShopifyAPIError(Exception):
    """Shopify API error."""

    pass


class ShopifyClient:
    """
    Shopify Admin REST API client with rate limiting and pagination support.
    """

    def __init__(
        self,
        shop: str,
        token: str,
        api_version: str = "2024-10",
        max_retries: int = 5,
    ):
        """
        Initialize Shopify client.

        Args:
            shop: Shop domain (e.g., my-shop.myshopify.com)
            token: Admin API access token
            api_version: API version (e.g., 2024-10)
            max_retries: Maximum number of retries for failed requests
        """
        self.shop = shop.replace("https://", "").replace("http://", "")
        self.token = token
        self.api_version = api_version
        self.max_retries = max_retries
        self.base_url = f"https://{self.shop}/admin/api/{api_version}"

        self.client = httpx.AsyncClient(
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        retry_count: int = 0,
    ) -> tuple[Any, Optional[str]]:
        """
        Make an API request with retry logic and rate limiting.

        Returns:
            Tuple of (response_json, next_page_info)
        """
        url = f"{self.base_url}/{endpoint}"

        try:
            response = await self.client.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
            )

            # Check rate limit
            rate_limit_header = response.headers.get("X-Shopify-Shop-Api-Call-Limit", "")
            calls_made, bucket_size = parse_rate_limit_header(rate_limit_header)

            if should_throttle(calls_made, bucket_size):
                # Approaching rate limit, sleep briefly
                sleep_time = 0.5
                logger.info(
                    f"Rate limit at {calls_made}/{bucket_size}, throttling for {sleep_time}s"
                )
                await asyncio.sleep(sleep_time)

            # Handle 429 (rate limited)
            if response.status_code == 429:
                if retry_count < self.max_retries:
                    retry_after = float(response.headers.get("Retry-After", 2))
                    logger.warning(f"Rate limited (429), retrying after {retry_after}s")
                    await asyncio.sleep(retry_after)
                    return await self._request(
                        method, endpoint, params, json_data, retry_count + 1
                    )
                else:
                    raise ShopifyAPIError(f"Rate limited after {self.max_retries} retries")

            # Handle 5xx errors with exponential backoff
            if 500 <= response.status_code < 600:
                if retry_count < self.max_retries:
                    delay = exponential_backoff_with_jitter(retry_count)
                    logger.warning(
                        f"Server error {response.status_code}, retrying in {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
                    return await self._request(
                        method, endpoint, params, json_data, retry_count + 1
                    )
                else:
                    raise ShopifyAPIError(
                        f"Server error {response.status_code} after {self.max_retries} retries"
                    )

            # Raise for other error status codes
            response.raise_for_status()

            # Parse pagination
            next_page_info = None
            link_header = response.headers.get("Link", "")
            if link_header:
                links = parse_link_header(link_header)
                next_page_info = links.get("next")

            return response.json(), next_page_info

        except httpx.HTTPStatusError as e:
            raise ShopifyAPIError(f"HTTP error {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            if retry_count < self.max_retries:
                delay = exponential_backoff_with_jitter(retry_count)
                logger.warning(f"Request error, retrying in {delay:.2f}s: {e}")
                await asyncio.sleep(delay)
                return await self._request(method, endpoint, params, json_data, retry_count + 1)
            raise ShopifyAPIError(f"Request error after {self.max_retries} retries: {e}")

    async def get_products(
        self,
        status: ProductStatus = ProductStatus.ACTIVE,
        limit: int = 250,
        page_info: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """
        Get products with cursor pagination.

        Args:
            status: Product status filter
            limit: Number of products per page (max 250)
            page_info: Cursor for pagination

        Returns:
            Tuple of (products_list, next_page_info)
        """
        params = {"limit": min(limit, 250)}

        if status != ProductStatus.ANY:
            params["status"] = status.value

        if page_info:
            params["page_info"] = page_info

        data, next_page_info = await self._request("GET", "products.json", params=params)
        products = data.get("products", [])

        logger.info(f"Fetched {len(products)} products (page_info: {page_info})")
        return products, next_page_info

    async def get_products_by_ids(
        self,
        product_ids: list[int],
        limit: int = 250,
    ) -> list[dict]:
        """
        Get products by IDs (up to 250 at a time).

        Args:
            product_ids: List of product IDs
            limit: Max IDs per request (max 250)

        Returns:
            List of products
        """
        if not product_ids:
            return []

        # Take only first 'limit' IDs
        ids_to_fetch = product_ids[:limit]
        ids_param = ",".join(map(str, ids_to_fetch))

        params = {"ids": ids_param, "limit": limit}

        data, _ = await self._request("GET", "products.json", params=params)
        products = data.get("products", [])

        logger.info(f"Fetched {len(products)} products by IDs")
        return products

    async def get_collects(
        self,
        collection_id: int,
        limit: int = 250,
        page_info: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str]]:
        """
        Get collects (product-collection relationships) with pagination.

        Args:
            collection_id: Collection ID
            limit: Number of collects per page (max 250)
            page_info: Cursor for pagination

        Returns:
            Tuple of (collects_list, next_page_info)
        """
        params = {
            "collection_id": collection_id,
            "limit": min(limit, 250),
        }

        if page_info:
            params["page_info"] = page_info

        data, next_page_info = await self._request("GET", "collects.json", params=params)
        collects = data.get("collects", [])

        logger.info(
            f"Fetched {len(collects)} collects for collection {collection_id} "
            f"(page_info: {page_info})"
        )
        return collects, next_page_info

    async def get_product_metafields(
        self,
        product_id: int,
        namespace: Optional[str] = None,
        key: Optional[str] = None,
    ) -> list[dict]:
        """
        Get metafields for a product.

        Args:
            product_id: Product ID
            namespace: Optional namespace filter
            key: Optional key filter

        Returns:
            List of metafields
        """
        params = {}
        if namespace:
            params["namespace"] = namespace
        if key:
            params["key"] = key

        endpoint = f"products/{product_id}/metafields.json"
        data, _ = await self._request("GET", endpoint, params=params)
        metafields = data.get("metafields", [])

        return metafields

    async def update_product_status(
        self,
        product_id: int,
        status: str,
    ) -> dict:
        """
        Update product status.

        Args:
            product_id: Product ID
            status: New status (draft, active, archived)

        Returns:
            Updated product data
        """
        endpoint = f"products/{product_id}.json"
        json_data = {"product": {"id": product_id, "status": status}}

        data, _ = await self._request("PUT", endpoint, json_data=json_data)
        product = data.get("product", {})

        logger.info(f"Updated product {product_id} status to {status}")
        return product
