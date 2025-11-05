"""Tests for utility functions."""

import pytest

from src.utils import (
    chunk_list,
    extract_urls,
    parse_link_header,
    parse_rate_limit_header,
    should_throttle,
)


class TestExtractUrls:
    """Tests for URL extraction."""

    def test_extract_single_url(self):
        text = "Check out this video: https://example.com/video.mp4"
        urls = extract_urls(text)
        assert urls == ["https://example.com/video.mp4"]

    def test_extract_multiple_urls(self):
        text = "Videos: https://example.com/1.mp4 and https://example.com/2.mp4"
        urls = extract_urls(text)
        assert set(urls) == {"https://example.com/1.mp4", "https://example.com/2.mp4"}

    def test_extract_url_with_trailing_punctuation(self):
        text = "Check this: https://example.com/video.mp4."
        urls = extract_urls(text)
        assert urls == ["https://example.com/video.mp4"]

    def test_extract_url_from_html(self):
        text = '<a href="https://example.com/video.mp4">Video</a>'
        urls = extract_urls(text)
        assert "https://example.com/video.mp4" in urls

    def test_extract_url_with_html_entities(self):
        text = "https://example.com/video?id=123&amp;type=mp4"
        urls = extract_urls(text)
        assert len(urls) == 1
        assert "https://example.com/video?id=123&type=mp4" in urls

    def test_no_urls(self):
        text = "No URLs in this text"
        urls = extract_urls(text)
        assert urls == []

    def test_empty_text(self):
        urls = extract_urls("")
        assert urls == []


class TestParseLinkHeader:
    """Tests for Link header parsing."""

    def test_parse_next_link(self):
        header = '<https://shop.myshopify.com/admin/api/2024-10/products.json?page_info=abc123>; rel="next"'
        links = parse_link_header(header)
        assert links["next"] == "abc123"

    def test_parse_previous_link(self):
        header = '<https://shop.myshopify.com/admin/api/2024-10/products.json?page_info=xyz789>; rel="previous"'
        links = parse_link_header(header)
        assert links["previous"] == "xyz789"

    def test_parse_multiple_links(self):
        header = (
            '<https://shop.myshopify.com/admin/api/2024-10/products.json?page_info=abc>; rel="next", '
            '<https://shop.myshopify.com/admin/api/2024-10/products.json?page_info=xyz>; rel="previous"'
        )
        links = parse_link_header(header)
        assert links["next"] == "abc"
        assert links["previous"] == "xyz"

    def test_empty_header(self):
        links = parse_link_header("")
        assert links == {}


class TestParseRateLimitHeader:
    """Tests for rate limit header parsing."""

    def test_parse_valid_header(self):
        calls_made, bucket_size = parse_rate_limit_header("32/40")
        assert calls_made == 32
        assert bucket_size == 40

    def test_parse_empty_header(self):
        calls_made, bucket_size = parse_rate_limit_header("")
        assert calls_made == 0
        assert bucket_size == 40

    def test_parse_invalid_header(self):
        calls_made, bucket_size = parse_rate_limit_header("invalid")
        assert calls_made == 0
        assert bucket_size == 40


class TestShouldThrottle:
    """Tests for throttle detection."""

    def test_should_throttle_above_threshold(self):
        # 32/40 = 0.8 = threshold
        assert should_throttle(32, 40, threshold=0.8)

    def test_should_throttle_below_threshold(self):
        # 20/40 = 0.5 < 0.8
        assert not should_throttle(20, 40, threshold=0.8)

    def test_should_throttle_zero_bucket(self):
        assert not should_throttle(10, 0)


class TestChunkList:
    """Tests for list chunking."""

    def test_chunk_list(self):
        items = list(range(10))
        chunks = chunk_list(items, 3)
        assert chunks == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]

    def test_chunk_list_exact_size(self):
        items = list(range(9))
        chunks = chunk_list(items, 3)
        assert chunks == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]

    def test_chunk_empty_list(self):
        chunks = chunk_list([], 3)
        assert chunks == []
