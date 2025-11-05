"""Utility functions for the Shopify link checker."""

import re
import time
from html import unescape
from typing import Any


def extract_urls(text: str) -> list[str]:
    """
    Extract all URLs from text using regex.

    Handles HTML entities, strips trailing punctuation, and normalizes.
    """
    if not text:
        return []

    # Unescape HTML entities
    text = unescape(text)

    # Find all URLs
    url_pattern = r"https?://[^\s\"<>]+"
    urls = re.findall(url_pattern, text, re.IGNORECASE)

    # Clean up URLs (remove trailing punctuation)
    cleaned_urls = []
    for url in urls:
        # Remove trailing punctuation like periods, commas, etc.
        url = re.sub(r"[.,;:!?)]+$", "", url)
        cleaned_urls.append(url)

    return list(set(cleaned_urls))  # Deduplicate


def parse_link_header(link_header: str) -> dict[str, str]:
    """
    Parse Shopify Link header for pagination.

    Returns dict with 'next' and 'previous' page_info values if present.
    """
    links = {}
    if not link_header:
        return links

    # Split by comma to get individual links
    parts = link_header.split(",")

    for part in parts:
        # Parse: <https://...?page_info=XXX>; rel="next"
        match = re.match(r'<([^>]+)>;\s*rel="([^"]+)"', part.strip())
        if match:
            url, rel = match.groups()
            # Extract page_info from URL
            page_info_match = re.search(r"page_info=([^&]+)", url)
            if page_info_match:
                links[rel] = page_info_match.group(1)

    return links


def exponential_backoff_with_jitter(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0):
    """
    Calculate exponential backoff delay with jitter.

    Args:
        attempt: Retry attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
    """
    import random

    delay = min(base_delay * (2**attempt), max_delay)
    # Add jitter: random value between 0 and delay
    jittered_delay = delay * random.random()
    return jittered_delay


def parse_rate_limit_header(header: str) -> tuple[int, int]:
    """
    Parse Shopify rate limit header.

    Header format: "32/40" means 32 calls made out of 40 bucket size.
    Returns (calls_made, bucket_size).
    """
    if not header:
        return (0, 40)

    try:
        parts = header.split("/")
        if len(parts) == 2:
            return (int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        pass

    return (0, 40)


def should_throttle(calls_made: int, bucket_size: int, threshold: float = 0.8) -> bool:
    """
    Determine if we should throttle based on rate limit usage.

    Args:
        calls_made: Number of API calls made
        bucket_size: Size of the rate limit bucket
        threshold: Percentage threshold (0.0-1.0) to start throttling
    """
    if bucket_size == 0:
        return False

    usage_ratio = calls_made / bucket_size
    return usage_ratio >= threshold


def chunk_list(items: list[Any], chunk_size: int) -> list[list[Any]]:
    """Split a list into chunks of specified size."""
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
