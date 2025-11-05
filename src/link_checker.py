"""URL validation logic for the link checker."""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class LinkCheckResult:
    """Result of checking a URL."""

    def __init__(
        self,
        url: str,
        is_broken: bool,
        http_status: Optional[int] = None,
        error: Optional[str] = None,
    ):
        self.url = url
        self.is_broken = is_broken
        self.http_status = http_status
        self.error = error


class LinkChecker:
    """
    Validates URLs with configurable timeout and redirect handling.
    """

    def __init__(
        self,
        timeout_ms: int = 8000,
        follow_redirects: bool = True,
        concurrency: int = 20,
        max_redirects: int = 10,
    ):
        """
        Initialize link checker.

        Args:
            timeout_ms: Request timeout in milliseconds
            follow_redirects: Whether to follow redirects
            concurrency: Maximum concurrent requests
            max_redirects: Maximum number of redirects to follow
        """
        self.timeout_ms = timeout_ms
        self.follow_redirects = follow_redirects
        self.concurrency = concurrency
        self.max_redirects = max_redirects

        # Semaphore to limit concurrency
        self.semaphore = asyncio.Semaphore(concurrency)

        # HTTP client for URL checking
        self.client = httpx.AsyncClient(
            timeout=timeout_ms / 1000.0,
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            verify=True,
        )

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def check_url(self, url: str) -> LinkCheckResult:
        """
        Check if a URL is accessible.

        A URL is considered broken if:
        - HTTP status >= 400
        - Network/DNS/SSL error occurs
        - Request times out
        - Too many redirects

        Args:
            url: URL to check

        Returns:
            LinkCheckResult with status and error info
        """
        async with self.semaphore:
            try:
                # Use GET (not HEAD) as specified
                response = await self.client.get(url)

                # Check if status indicates broken link
                is_broken = response.status_code >= 400

                return LinkCheckResult(
                    url=url,
                    is_broken=is_broken,
                    http_status=response.status_code,
                    error=None if not is_broken else f"HTTP {response.status_code}",
                )

            except httpx.TooManyRedirects:
                logger.warning(f"Too many redirects for URL: {url}")
                return LinkCheckResult(
                    url=url,
                    is_broken=True,
                    http_status=None,
                    error="Too many redirects",
                )

            except httpx.TimeoutException:
                logger.warning(f"Timeout checking URL: {url}")
                return LinkCheckResult(
                    url=url,
                    is_broken=True,
                    http_status=None,
                    error="Request timeout",
                )

            except httpx.HTTPError as e:
                logger.warning(f"HTTP error checking URL {url}: {e}")
                return LinkCheckResult(
                    url=url,
                    is_broken=True,
                    http_status=None,
                    error=f"HTTP error: {type(e).__name__}",
                )

            except Exception as e:
                logger.error(f"Unexpected error checking URL {url}: {e}")
                return LinkCheckResult(
                    url=url,
                    is_broken=True,
                    http_status=None,
                    error=f"Error: {type(e).__name__}",
                )

    async def check_urls(self, urls: list[str]) -> list[LinkCheckResult]:
        """
        Check multiple URLs concurrently.

        Args:
            urls: List of URLs to check

        Returns:
            List of LinkCheckResult objects
        """
        if not urls:
            return []

        tasks = [self.check_url(url) for url in urls]
        results = await asyncio.gather(*tasks)
        return results
