"""Tests for link checker."""

import pytest
from unittest.mock import AsyncMock, Mock, patch

from src.link_checker import LinkChecker


@pytest.mark.asyncio
class TestLinkChecker:
    """Tests for LinkChecker."""

    async def test_check_url_success(self):
        checker = LinkChecker()

        # Mock successful response
        with patch.object(checker.client, "get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = await checker.check_url("https://example.com")

            assert not result.is_broken
            assert result.http_status == 200
            assert result.error is None

        await checker.close()

    async def test_check_url_broken(self):
        checker = LinkChecker()

        # Mock 404 response
        with patch.object(checker.client, "get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            result = await checker.check_url("https://example.com/notfound")

            assert result.is_broken
            assert result.http_status == 404
            assert result.error == "HTTP 404"

        await checker.close()

    async def test_check_url_timeout(self):
        import httpx

        checker = LinkChecker(timeout_ms=1000)

        # Mock timeout
        with patch.object(checker.client, "get") as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Timeout")

            result = await checker.check_url("https://slow-site.com")

            assert result.is_broken
            assert result.http_status is None
            assert "timeout" in result.error.lower()

        await checker.close()

    async def test_check_multiple_urls(self):
        checker = LinkChecker()

        # Mock responses for multiple URLs
        with patch.object(checker.client, "get") as mock_get:
            def side_effect(url):
                mock_response = Mock()
                if "good" in url:
                    mock_response.status_code = 200
                else:
                    mock_response.status_code = 404
                return mock_response

            mock_get.side_effect = side_effect

            urls = ["https://example.com/good", "https://example.com/bad"]
            results = await checker.check_urls(urls)

            assert len(results) == 2
            assert not results[0].is_broken
            assert results[1].is_broken

        await checker.close()
