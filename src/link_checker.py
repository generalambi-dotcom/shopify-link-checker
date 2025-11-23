"""URL validation logic for the link checker with redirect tracking."""

import asyncio
import logging
import ssl
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from .models import LinkStatus

logger = logging.getLogger(__name__)


@dataclass
class LinkCheckResult:
    """Result of checking a URL with full redirect information."""

    original_url: str
    final_url: Optional[str]
    is_broken: bool
    link_status: LinkStatus
    http_status: Optional[int] = None
    was_redirected: bool = False
    redirect_count: int = 0
    error: Optional[str] = None

    # Legacy compatibility
    @property
    def url(self) -> str:
        return self.original_url

    @classmethod
    def classify_status(
        cls,
        original_url: str,
        status_code: Optional[int],
        final_url: Optional[str],
        redirect_count: int,
        error: Optional[str] = None,
        error_type: Optional[str] = None,
    ) -> "LinkCheckResult":
        """Classify the link status based on response."""
        was_redirected = redirect_count > 0 or (final_url and final_url != original_url)

        # Handle errors
        if error:
            if error_type == "timeout":
                link_status = LinkStatus.BROKEN_TIMEOUT
            elif error_type == "dns":
                link_status = LinkStatus.BROKEN_DNS
            elif error_type == "ssl":
                link_status = LinkStatus.BROKEN_SSL
            elif error_type == "too_many_redirects":
                link_status = LinkStatus.BROKEN_TOO_MANY_REDIRECTS
            else:
                link_status = LinkStatus.BROKEN_OTHER

            return cls(
                original_url=original_url,
                final_url=final_url,
                is_broken=True,
                link_status=link_status,
                http_status=status_code,
                was_redirected=was_redirected,
                redirect_count=redirect_count,
                error=error,
            )

        # Handle status codes
        if status_code is None:
            return cls(
                original_url=original_url,
                final_url=final_url,
                is_broken=True,
                link_status=LinkStatus.BROKEN_OTHER,
                http_status=None,
                was_redirected=was_redirected,
                redirect_count=redirect_count,
                error="No response received",
            )

        # 2xx - Success
        if 200 <= status_code < 300:
            link_status = LinkStatus.REDIRECTED_OK if was_redirected else LinkStatus.OK
            return cls(
                original_url=original_url,
                final_url=final_url,
                is_broken=False,
                link_status=link_status,
                http_status=status_code,
                was_redirected=was_redirected,
                redirect_count=redirect_count,
            )

        # 3xx - Redirect (without follow_redirects)
        if 300 <= status_code < 400:
            return cls(
                original_url=original_url,
                final_url=final_url,
                is_broken=False,
                link_status=LinkStatus.REDIRECTED_OK,
                http_status=status_code,
                was_redirected=True,
                redirect_count=redirect_count,
            )

        # 404 - Not Found
        if status_code == 404:
            return cls(
                original_url=original_url,
                final_url=final_url,
                is_broken=True,
                link_status=LinkStatus.BROKEN_NOT_FOUND,
                http_status=status_code,
                was_redirected=was_redirected,
                redirect_count=redirect_count,
                error=f"HTTP {status_code} Not Found",
            )

        # 4xx - Client Error
        if 400 <= status_code < 500:
            return cls(
                original_url=original_url,
                final_url=final_url,
                is_broken=True,
                link_status=LinkStatus.BROKEN_CLIENT_ERROR,
                http_status=status_code,
                was_redirected=was_redirected,
                redirect_count=redirect_count,
                error=f"HTTP {status_code} Client Error",
            )

        # 5xx - Server Error
        if 500 <= status_code < 600:
            return cls(
                original_url=original_url,
                final_url=final_url,
                is_broken=True,
                link_status=LinkStatus.BROKEN_SERVER_ERROR,
                http_status=status_code,
                was_redirected=was_redirected,
                redirect_count=redirect_count,
                error=f"HTTP {status_code} Server Error",
            )

        # Unknown status
        return cls(
            original_url=original_url,
            final_url=final_url,
            is_broken=True,
            link_status=LinkStatus.BROKEN_OTHER,
            http_status=status_code,
            was_redirected=was_redirected,
            redirect_count=redirect_count,
            error=f"HTTP {status_code} Unknown Status",
        )


class LinkChecker:
    """
    Validates URLs with configurable timeout, redirect handling, and detailed tracking.
    """

    def __init__(
        self,
        timeout_ms: int = 8000,
        follow_redirects: bool = True,
        concurrency: int = 20,
        max_redirects: int = 5,
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

        # HTTP client - we track redirects manually for detailed info
        self.client = httpx.AsyncClient(
            timeout=timeout_ms / 1000.0,
            follow_redirects=False,  # Manual tracking
            verify=True,
        )

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

    async def check_url(self, url: str) -> LinkCheckResult:
        """
        Check if a URL is accessible with full redirect tracking.

        Args:
            url: URL to check

        Returns:
            LinkCheckResult with detailed status and redirect info
        """
        async with self.semaphore:
            original_url = url
            current_url = url
            redirect_count = 0

            try:
                while True:
                    try:
                        response = await self.client.get(current_url)
                    except httpx.TimeoutException:
                        return LinkCheckResult.classify_status(
                            original_url=original_url,
                            status_code=None,
                            final_url=current_url if current_url != original_url else None,
                            redirect_count=redirect_count,
                            error="Request timeout",
                            error_type="timeout",
                        )
                    except httpx.ConnectError as e:
                        error_str = str(e).lower()
                        if "name or service not known" in error_str or "getaddrinfo" in error_str:
                            error_type = "dns"
                            error_msg = "DNS resolution failed"
                        else:
                            error_type = "other"
                            error_msg = f"Connection error: {type(e).__name__}"

                        return LinkCheckResult.classify_status(
                            original_url=original_url,
                            status_code=None,
                            final_url=current_url if current_url != original_url else None,
                            redirect_count=redirect_count,
                            error=error_msg,
                            error_type=error_type,
                        )

                    # Check for redirect
                    if 300 <= response.status_code < 400:
                        if not self.follow_redirects:
                            return LinkCheckResult.classify_status(
                                original_url=original_url,
                                status_code=response.status_code,
                                final_url=response.headers.get("location"),
                                redirect_count=redirect_count,
                            )

                        redirect_count += 1
                        if redirect_count > self.max_redirects:
                            return LinkCheckResult.classify_status(
                                original_url=original_url,
                                status_code=response.status_code,
                                final_url=current_url,
                                redirect_count=redirect_count,
                                error=f"Too many redirects ({redirect_count})",
                                error_type="too_many_redirects",
                            )

                        location = response.headers.get("location")
                        if not location:
                            return LinkCheckResult.classify_status(
                                original_url=original_url,
                                status_code=response.status_code,
                                final_url=current_url,
                                redirect_count=redirect_count,
                                error="Redirect without location header",
                                error_type="other",
                            )

                        # Handle relative URLs
                        if location.startswith("/"):
                            parsed = urlparse(current_url)
                            location = f"{parsed.scheme}://{parsed.netloc}{location}"

                        current_url = location
                        continue

                    # Non-redirect response
                    final_url = current_url if current_url != original_url else None
                    return LinkCheckResult.classify_status(
                        original_url=original_url,
                        status_code=response.status_code,
                        final_url=final_url,
                        redirect_count=redirect_count,
                    )

            except httpx.TooManyRedirects:
                logger.warning(f"Too many redirects for URL: {url}")
                return LinkCheckResult.classify_status(
                    original_url=original_url,
                    status_code=None,
                    final_url=current_url if current_url != original_url else None,
                    redirect_count=redirect_count,
                    error="Too many redirects",
                    error_type="too_many_redirects",
                )

            except ssl.SSLError as e:
                return LinkCheckResult.classify_status(
                    original_url=original_url,
                    status_code=None,
                    final_url=current_url if current_url != original_url else None,
                    redirect_count=redirect_count,
                    error=f"SSL error: {e}",
                    error_type="ssl",
                )

            except httpx.HTTPError as e:
                logger.warning(f"HTTP error checking URL {url}: {e}")
                error_type = "ssl" if "ssl" in str(e).lower() else "other"
                return LinkCheckResult.classify_status(
                    original_url=original_url,
                    status_code=None,
                    final_url=current_url if current_url != original_url else None,
                    redirect_count=redirect_count,
                    error=f"HTTP error: {type(e).__name__}",
                    error_type=error_type,
                )

            except Exception as e:
                logger.error(f"Unexpected error checking URL {url}: {e}")
                return LinkCheckResult.classify_status(
                    original_url=original_url,
                    status_code=None,
                    final_url=current_url if current_url != original_url else None,
                    redirect_count=redirect_count,
                    error=f"Error: {type(e).__name__}: {str(e)}",
                    error_type="other",
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
        return list(results)
