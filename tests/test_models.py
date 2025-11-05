"""Tests for data models."""

import pytest

from src.models import Checkpoint, JobConfig, ProductStatus


class TestCheckpoint:
    """Tests for Checkpoint model."""

    def test_compute_scope_hash(self):
        hash1 = Checkpoint.compute_scope_hash(
            shop="shop1.myshopify.com",
            status=ProductStatus.ACTIVE,
            namespace="custom",
            key="video_url",
            collection_ids=None,
        )

        # Same params should produce same hash
        hash2 = Checkpoint.compute_scope_hash(
            shop="shop1.myshopify.com",
            status=ProductStatus.ACTIVE,
            namespace="custom",
            key="video_url",
            collection_ids=None,
        )

        assert hash1 == hash2

    def test_scope_hash_different_shop(self):
        hash1 = Checkpoint.compute_scope_hash(
            shop="shop1.myshopify.com",
            status=ProductStatus.ACTIVE,
            namespace="custom",
            key="video_url",
            collection_ids=None,
        )

        hash2 = Checkpoint.compute_scope_hash(
            shop="shop2.myshopify.com",
            status=ProductStatus.ACTIVE,
            namespace="custom",
            key="video_url",
            collection_ids=None,
        )

        assert hash1 != hash2

    def test_scope_hash_with_collections(self):
        hash1 = Checkpoint.compute_scope_hash(
            shop="shop.myshopify.com",
            status=ProductStatus.ACTIVE,
            namespace="custom",
            key="video_url",
            collection_ids=[123, 456],
        )

        # Different collection order should produce same hash (sorted)
        hash2 = Checkpoint.compute_scope_hash(
            shop="shop.myshopify.com",
            status=ProductStatus.ACTIVE,
            namespace="custom",
            key="video_url",
            collection_ids=[456, 123],
        )

        assert hash1 == hash2


class TestJobConfig:
    """Tests for JobConfig model."""

    def test_valid_config(self):
        config = JobConfig(
            shop="shop.myshopify.com",
            token="shpat_xxx",
            namespace="custom",
            key="video_url",
        )

        assert config.shop == "shop.myshopify.com"
        assert config.batch_size == 250  # default
        assert config.status == ProductStatus.ACTIVE  # default

    def test_batch_size_validation(self):
        # Should allow max 250
        config = JobConfig(
            shop="shop.myshopify.com",
            token="shpat_xxx",
            namespace="custom",
            key="video_url",
            batch_size=250,
        )
        assert config.batch_size == 250

        # Should raise on > 250
        with pytest.raises(Exception):
            JobConfig(
                shop="shop.myshopify.com",
                token="shpat_xxx",
                namespace="custom",
                key="video_url",
                batch_size=300,
            )

    def test_get_scope_hash(self):
        config = JobConfig(
            shop="shop.myshopify.com",
            token="shpat_xxx",
            namespace="custom",
            key="video_url",
            status=ProductStatus.ACTIVE,
        )

        scope_hash = config.get_scope_hash()
        assert isinstance(scope_hash, str)
        assert len(scope_hash) == 64  # SHA256 hex digest length
