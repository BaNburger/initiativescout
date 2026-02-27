"""Tests for the Crawl4AI + DuckDuckGo enrichment pipeline.

Covers: rate limiter, extra links enrichment, DuckDuckGo discovery,
Crawl4AI integration, open_crawler context manager, and services integration.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from scout.models import Base, Enrichment, Initiative


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture()
def sample_initiative(session: Session) -> Initiative:
    init = Initiative(
        name="TestBot", uni="TUM", sector="AI",
        description="A test initiative", website="https://testbot.dev",
        github_org="testbot-org",
        team_page="https://testbot.dev/team",
        linkedin="https://linkedin.com/company/testbot",
        extra_links_json=json.dumps({
            "instagram": "https://instagram.com/testbot",
            "huggingface": "https://huggingface.co/testbot",
            "x_twitter": "https://x.com/testbot",
            "linkedin_urls": "https://linkedin.com/company/testbot",
            "website_urls": "https://testbot.dev",  # should be skipped (overlap)
            "github_urls": "https://github.com/testbot-org",  # should be skipped
        }),
    )
    session.add(init)
    session.flush()
    return init


@pytest.fixture()
def empty_initiative(session: Session) -> Initiative:
    init = Initiative(name="EmptyInit", uni="LMU")
    session.add(init)
    session.flush()
    return init


# ---------------------------------------------------------------------------
# Tests: _DDGRateLimiter
# ---------------------------------------------------------------------------


class TestDDGRateLimiter:
    def test_initial_state(self):
        from scout.enricher import _DDGRateLimiter
        limiter = _DDGRateLimiter(min_delay=1.0, max_delay=10.0)
        assert limiter._current_delay == 1.0
        assert limiter._max_delay == 10.0
        assert limiter._last_call == 0.0

    def test_backoff_doubles_delay(self):
        from scout.enricher import _DDGRateLimiter
        limiter = _DDGRateLimiter(min_delay=2.0, max_delay=16.0)
        limiter.backoff()
        assert limiter._current_delay == 4.0
        limiter.backoff()
        assert limiter._current_delay == 8.0
        limiter.backoff()
        assert limiter._current_delay == 16.0
        # Should cap at max
        limiter.backoff()
        assert limiter._current_delay == 16.0

    def test_reset_restores_baseline(self):
        from scout.enricher import _DDGRateLimiter
        limiter = _DDGRateLimiter(min_delay=2.0, max_delay=16.0)
        limiter.backoff()
        limiter.backoff()
        assert limiter._current_delay == 8.0
        limiter.reset()
        assert limiter._current_delay == 2.0

    @pytest.mark.asyncio
    async def test_acquire_enforces_delay(self):
        from scout.enricher import _DDGRateLimiter
        limiter = _DDGRateLimiter(min_delay=0.1, max_delay=1.0)
        # First call should be instant
        start = time.monotonic()
        await limiter.acquire()
        elapsed1 = time.monotonic() - start
        assert elapsed1 < 0.2

        # Second call should wait ~0.1s
        start = time.monotonic()
        await limiter.acquire()
        elapsed2 = time.monotonic() - start
        assert elapsed2 >= 0.05  # allow some tolerance


# ---------------------------------------------------------------------------
# Tests: enrich_extra_links
# ---------------------------------------------------------------------------


class TestEnrichExtraLinks:
    @pytest.mark.asyncio
    async def test_skips_overlapping_keys(self, sample_initiative):
        """Keys like website_urls and github_urls should be skipped."""
        from scout.enricher import enrich_extra_links

        with patch("scout.enricher._enrich_page", new_callable=AsyncMock) as mock_enrich:
            mock_enrich.return_value = None
            await enrich_extra_links(sample_initiative, crawler=None)

            # Check that website_urls and github_urls were NOT called
            called_source_types = [
                call.args[2] for call in mock_enrich.call_args_list
            ]
            assert "website" not in called_source_types or \
                all(c.args[1] != "https://testbot.dev" for c in mock_enrich.call_args_list)
            assert "github" not in called_source_types or \
                all(c.args[1] != "https://github.com/testbot-org" for c in mock_enrich.call_args_list)

    @pytest.mark.asyncio
    async def test_crawls_valid_extra_links(self, sample_initiative):
        """Should crawl instagram, huggingface, x_twitter, linkedin_urls."""
        from scout.enricher import enrich_extra_links

        fake_enrichment = Enrichment(
            initiative_id=sample_initiative.id,
            source_type="test",
            raw_text="test content",
            summary="test",
            fetched_at=datetime.now(UTC),
        )

        with patch("scout.enricher._enrich_page", new_callable=AsyncMock) as mock_enrich:
            mock_enrich.return_value = fake_enrichment
            results = await enrich_extra_links(sample_initiative, crawler=None)

            # Should have crawled instagram, huggingface, x_twitter, linkedin_urls
            called_urls = {call.args[1] for call in mock_enrich.call_args_list}
            assert "https://instagram.com/testbot" in called_urls
            assert "https://huggingface.co/testbot" in called_urls
            assert "https://x.com/testbot" in called_urls

    @pytest.mark.asyncio
    async def test_strips_url_suffix_from_source_type(self, sample_initiative):
        """linkedin_urls should become source_type='linkedin'."""
        from scout.enricher import enrich_extra_links

        with patch("scout.enricher._enrich_page", new_callable=AsyncMock) as mock_enrich:
            mock_enrich.return_value = None
            await enrich_extra_links(sample_initiative, crawler=None)

            called_source_types = [
                call.args[2] for call in mock_enrich.call_args_list
            ]
            # linkedin_urls -> linkedin
            assert "linkedin" in called_source_types
            # instagram stays instagram (no suffix)
            assert "instagram" in called_source_types

    @pytest.mark.asyncio
    async def test_empty_extra_links(self, empty_initiative):
        """Should return empty list when no extra links."""
        from scout.enricher import enrich_extra_links
        results = await enrich_extra_links(empty_initiative, crawler=None)
        assert results == []

    @pytest.mark.asyncio
    async def test_handles_exceptions_gracefully(self, sample_initiative):
        """Should not raise if individual crawls fail."""
        from scout.enricher import enrich_extra_links

        with patch("scout.enricher._enrich_page", new_callable=AsyncMock) as mock_enrich:
            mock_enrich.side_effect = Exception("network error")
            results = await enrich_extra_links(sample_initiative, crawler=None)
            assert results == []


# ---------------------------------------------------------------------------
# Tests: discover_urls
# ---------------------------------------------------------------------------


class TestDiscoverUrls:
    @pytest.mark.asyncio
    async def test_discovers_platform_urls(self, sample_initiative):
        """Should extract platform URLs from DDG results."""
        from scout.enricher import discover_urls

        fake_results = [
            {"href": "https://crunchbase.com/organization/testbot", "title": "TestBot", "body": "..."},
            {"href": "https://youtube.com/c/testbot", "title": "TestBot YouTube", "body": "..."},
            {"href": "https://example.com/irrelevant", "title": "Other", "body": "..."},
        ]

        with patch("scout.enricher._DDGS_AVAILABLE", True), \
             patch("scout.enricher._ddg_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = fake_results
            discovered = await discover_urls(sample_initiative)

        # crunchbase and youtube should be discovered (not in existing extra_links)
        assert "crunchbase" in discovered
        assert "youtube" in discovered
        # instagram already exists in extra_links_json, should NOT be discovered
        assert "instagram" not in discovered

    @pytest.mark.asyncio
    async def test_skips_already_known_urls(self, sample_initiative):
        """URLs already in extra_links_json should not be rediscovered."""
        from scout.enricher import discover_urls

        fake_results = [
            {"href": "https://instagram.com/testbot_official", "title": "TestBot", "body": "..."},
        ]

        with patch("scout.enricher._DDGS_AVAILABLE", True), \
             patch("scout.enricher._ddg_search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = fake_results
            discovered = await discover_urls(sample_initiative)

        assert "instagram" not in discovered

    @pytest.mark.asyncio
    async def test_returns_empty_for_nameless_initiative(self, session):
        """Should return empty dict when initiative has no name."""
        from scout.enricher import discover_urls

        init = Initiative(name="", uni="TUM")
        session.add(init)
        session.flush()

        with patch("scout.enricher._DDGS_AVAILABLE", True):
            discovered = await discover_urls(init)
        assert discovered == {}

    @pytest.mark.asyncio
    async def test_raises_without_ddg_dependency(self, sample_initiative):
        """Should raise ImportError when duckduckgo-search is not installed."""
        from scout.enricher import discover_urls

        with patch("scout.enricher._DDGS_AVAILABLE", False):
            with pytest.raises(ImportError, match="duckduckgo-search"):
                await discover_urls(sample_initiative)

    @pytest.mark.asyncio
    async def test_handles_search_failure(self, sample_initiative):
        """Should return empty dict on search failure."""
        from scout.enricher import discover_urls

        with patch("scout.enricher._DDGS_AVAILABLE", True), \
             patch("scout.enricher._ddg_search", new_callable=AsyncMock) as mock_search:
            mock_search.side_effect = Exception("network error")
            discovered = await discover_urls(sample_initiative)
        assert discovered == {}


# ---------------------------------------------------------------------------
# Tests: open_crawler
# ---------------------------------------------------------------------------


class TestOpenCrawler:
    @pytest.mark.asyncio
    async def test_yields_none_without_crawl4ai(self):
        """Should yield None when crawl4ai is not installed."""
        from scout.enricher import open_crawler

        with patch("scout.enricher._CRAWL4AI_AVAILABLE", False):
            async with open_crawler() as crawler:
                assert crawler is None

    @pytest.mark.asyncio
    async def test_yields_crawler_with_crawl4ai(self):
        """Should yield AsyncWebCrawler when crawl4ai is available."""
        from scout.enricher import open_crawler

        mock_crawler_instance = AsyncMock()
        mock_crawler_instance.__aenter__ = AsyncMock(return_value=mock_crawler_instance)
        mock_crawler_instance.__aexit__ = AsyncMock(return_value=False)
        mock_browser_config = MagicMock()

        with patch("scout.enricher._CRAWL4AI_AVAILABLE", True), \
             patch("scout.enricher.BrowserConfig", mock_browser_config, create=True), \
             patch("scout.enricher.AsyncWebCrawler", return_value=mock_crawler_instance, create=True):
            async with open_crawler() as crawler:
                assert crawler is mock_crawler_instance


# ---------------------------------------------------------------------------
# Tests: _crawl4ai_fetch
# ---------------------------------------------------------------------------


class TestCrawl4aiFetch:
    """Tests for _crawl4ai_fetch. Must mock CrawlerRunConfig since crawl4ai may not be installed."""

    def _patch_crawl4ai(self):
        """Context manager that ensures CrawlerRunConfig is available."""
        mock_config_class = MagicMock()
        return patch("scout.enricher.CrawlerRunConfig", mock_config_class, create=True)

    @pytest.mark.asyncio
    async def test_returns_markdown_on_success(self):
        """Should return fit_markdown from successful crawl."""
        from scout.enricher import _crawl4ai_fetch

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.markdown.fit_markdown = "# Test Page\n\nSome content here."
        mock_result.markdown.raw_markdown = "# Test Raw"

        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(return_value=mock_result)

        with self._patch_crawl4ai():
            text = await _crawl4ai_fetch("https://example.com", mock_crawler)
        assert text == "# Test Page\n\nSome content here."

    @pytest.mark.asyncio
    async def test_falls_back_to_raw_markdown(self):
        """Should use raw_markdown when fit_markdown is empty."""
        from scout.enricher import _crawl4ai_fetch

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.markdown.fit_markdown = ""
        mock_result.markdown.raw_markdown = "# Raw Content"

        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(return_value=mock_result)

        with self._patch_crawl4ai():
            text = await _crawl4ai_fetch("https://example.com", mock_crawler)
        assert text == "# Raw Content"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        """Should return None when crawl fails."""
        from scout.enricher import _crawl4ai_fetch

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error_message = "timeout"

        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(return_value=mock_result)

        with self._patch_crawl4ai():
            text = await _crawl4ai_fetch("https://example.com", mock_crawler)
        assert text is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        """Should return None on exception rather than raising."""
        from scout.enricher import _crawl4ai_fetch

        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(side_effect=Exception("browser crash"))

        with self._patch_crawl4ai():
            text = await _crawl4ai_fetch("https://example.com", mock_crawler)
        assert text is None

    @pytest.mark.asyncio
    async def test_truncates_to_max_text(self):
        """Should cap returned text at _MAX_TEXT characters."""
        from scout.enricher import _MAX_TEXT, _crawl4ai_fetch

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.markdown.fit_markdown = "x" * (_MAX_TEXT + 5000)
        mock_result.markdown.raw_markdown = ""

        mock_crawler = AsyncMock()
        mock_crawler.arun = AsyncMock(return_value=mock_result)

        with self._patch_crawl4ai():
            text = await _crawl4ai_fetch("https://example.com", mock_crawler)
        assert len(text) == _MAX_TEXT


# ---------------------------------------------------------------------------
# Tests: _enrich_page with crawler
# ---------------------------------------------------------------------------


class TestEnrichPageWithCrawler:
    @pytest.mark.asyncio
    async def test_uses_crawl4ai_when_available(self, sample_initiative):
        """Should use Crawl4AI when crawler is provided and crawl4ai is available."""
        from scout.enricher import _enrich_page

        with patch("scout.enricher._CRAWL4AI_AVAILABLE", True), \
             patch("scout.enricher._crawl4ai_fetch", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = "# Crawled Content\nHello world"
            mock_crawler = MagicMock()

            enrichment = await _enrich_page(
                sample_initiative, "https://example.com", "test_source", mock_crawler,
            )

            assert enrichment is not None
            assert enrichment.source_type == "test_source"
            assert "Crawled Content" in enrichment.raw_text
            mock_fetch.assert_called_once_with("https://example.com", mock_crawler)

    @pytest.mark.asyncio
    async def test_falls_back_to_httpx(self, sample_initiative):
        """Should fall back to httpx when crawl4ai returns None."""
        from scout.enricher import _enrich_page

        with patch("scout.enricher._CRAWL4AI_AVAILABLE", True), \
             patch("scout.enricher._crawl4ai_fetch", new_callable=AsyncMock) as mock_c4a, \
             patch("scout.enricher._fetch_url", new_callable=AsyncMock) as mock_httpx, \
             patch("scout.enricher._extract_text") as mock_extract:
            mock_c4a.return_value = None  # Crawl4AI fails
            mock_httpx.return_value = "<html><body><p>Fallback content</p></body></html>"
            mock_extract.return_value = "CONTENT: Fallback content"

            enrichment = await _enrich_page(
                sample_initiative, "https://example.com", "test", MagicMock(),
            )

            assert enrichment is not None
            assert "Fallback content" in enrichment.raw_text
            mock_httpx.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_crawler_uses_httpx_directly(self, sample_initiative):
        """Should use httpx when no crawler is provided."""
        from scout.enricher import _enrich_page

        with patch("scout.enricher._fetch_url", new_callable=AsyncMock) as mock_httpx, \
             patch("scout.enricher._extract_text") as mock_extract:
            mock_httpx.return_value = "<html><body><p>Direct fetch</p></body></html>"
            mock_extract.return_value = "CONTENT: Direct fetch"

            enrichment = await _enrich_page(
                sample_initiative, "https://example.com", "test", None,
            )

            assert enrichment is not None
            mock_httpx.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: services.run_enrichment with crawler
# ---------------------------------------------------------------------------


class TestRunEnrichmentWithCrawler:
    @pytest.mark.asyncio
    async def test_passes_crawler_to_enrichers(self, session, sample_initiative):
        """run_enrichment should pass crawler to website, team_page, and extra_links."""
        from scout import services

        mock_crawler = MagicMock()
        fake_enrichment = Enrichment(
            initiative_id=sample_initiative.id,
            source_type="website",
            raw_text="test",
            summary="test",
            fetched_at=datetime.now(UTC),
        )

        with patch("scout.services.enrich_website", new_callable=AsyncMock) as mock_web, \
             patch("scout.services.enrich_team_page", new_callable=AsyncMock) as mock_team, \
             patch("scout.services.enrich_github", new_callable=AsyncMock) as mock_gh, \
             patch("scout.services.enrich_extra_links", new_callable=AsyncMock) as mock_extra:
            mock_web.return_value = fake_enrichment
            mock_team.return_value = None
            mock_gh.return_value = None
            mock_extra.return_value = []

            await services.run_enrichment(session, sample_initiative, crawler=mock_crawler)

            # Verify crawler was passed to website and team_page but not github
            mock_web.assert_called_once_with(sample_initiative, mock_crawler)
            mock_team.assert_called_once_with(sample_initiative, mock_crawler)
            mock_gh.assert_called_once_with(sample_initiative)  # no crawler
            mock_extra.assert_called_once_with(sample_initiative, mock_crawler)


# ---------------------------------------------------------------------------
# Tests: services.run_discovery
# ---------------------------------------------------------------------------


class TestRunDiscovery:
    @pytest.mark.asyncio
    async def test_merges_discovered_urls(self, session, sample_initiative):
        """run_discovery should merge discovered URLs into extra_links_json."""
        from scout import services

        with patch("scout.services.discover_urls", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {
                "crunchbase": "https://crunchbase.com/org/testbot",
                "youtube": "https://youtube.com/c/testbot",
            }
            result = await services.run_discovery(session, sample_initiative)

        assert result["urls_found"] == 2
        assert "crunchbase" in result["discovered_urls"]

        # Verify extra_links_json was updated
        extra = json.loads(sample_initiative.extra_links_json)
        assert "crunchbase" in extra
        assert "youtube" in extra
        # Original links preserved
        assert "instagram" in extra

    @pytest.mark.asyncio
    async def test_no_urls_discovered(self, session, sample_initiative):
        """run_discovery should not modify initiative when nothing found."""
        from scout import services

        original_json = sample_initiative.extra_links_json

        with patch("scout.services.discover_urls", new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = {}
            result = await services.run_discovery(session, sample_initiative)

        assert result["urls_found"] == 0
        assert sample_initiative.extra_links_json == original_json


# ---------------------------------------------------------------------------
# Tests: Scorer dossier source filters
# ---------------------------------------------------------------------------


class TestDossierSourceFilters:
    def test_team_dossier_includes_social_sources(self):
        """Team dossier should include linkedin, instagram, facebook in source_filter."""
        from scout.scorer import build_team_dossier

        init = Initiative(
            name="Test", uni="TUM", description="test",
            team_size="5", member_count=5,
        )
        enrichments = [
            Enrichment(source_type="linkedin", summary="LinkedIn profile data",
                       raw_text="LinkedIn data", fetched_at=datetime.now(UTC)),
            Enrichment(source_type="instagram", summary="Instagram presence",
                       raw_text="Instagram data", fetched_at=datetime.now(UTC)),
            Enrichment(source_type="huggingface", summary="HF models",
                       raw_text="HuggingFace data", fetched_at=datetime.now(UTC)),
        ]
        dossier = build_team_dossier(init, enrichments)

        assert "LINKEDIN DATA" in dossier
        assert "INSTAGRAM DATA" in dossier
        # HuggingFace should NOT be in team dossier
        assert "HUGGINGFACE DATA" not in dossier

    def test_tech_dossier_includes_research_sources(self):
        """Tech dossier should include huggingface, researchgate, etc."""
        from scout.scorer import build_tech_dossier

        init = Initiative(
            name="Test", uni="TUM", description="test",
            technology_domains="NLP",
        )
        enrichments = [
            Enrichment(source_type="huggingface", summary="HF models present",
                       raw_text="HuggingFace data", fetched_at=datetime.now(UTC)),
            Enrichment(source_type="researchgate", summary="Research papers",
                       raw_text="ResearchGate data", fetched_at=datetime.now(UTC)),
            Enrichment(source_type="linkedin", summary="LinkedIn data",
                       raw_text="LinkedIn data", fetched_at=datetime.now(UTC)),
        ]
        dossier = build_tech_dossier(init, enrichments)

        assert "HUGGINGFACE DATA" in dossier
        assert "RESEARCHGATE DATA" in dossier
        # LinkedIn should NOT be in tech dossier
        assert "LINKEDIN DATA" not in dossier

    def test_full_dossier_includes_all_sources(self):
        """Full/opportunity dossier should include ALL sources."""
        from scout.scorer import build_full_dossier

        init = Initiative(
            name="Test", uni="TUM", description="test",
            sector="AI",
        )
        enrichments = [
            Enrichment(source_type="linkedin", summary="LinkedIn data",
                       raw_text="LinkedIn data", fetched_at=datetime.now(UTC)),
            Enrichment(source_type="huggingface", summary="HF data",
                       raw_text="HuggingFace data", fetched_at=datetime.now(UTC)),
            Enrichment(source_type="crunchbase", summary="Crunchbase data",
                       raw_text="Crunchbase data", fetched_at=datetime.now(UTC)),
        ]
        dossier = build_full_dossier(init, enrichments)

        assert "LINKEDIN DATA" in dossier
        assert "HUGGINGFACE DATA" in dossier
        assert "CRUNCHBASE DATA" in dossier


# ---------------------------------------------------------------------------
# Tests: Module-level dependency detection
# ---------------------------------------------------------------------------


class TestDependencyDetection:
    def test_crawl4ai_flag_exists(self):
        from scout.enricher import _CRAWL4AI_AVAILABLE
        assert isinstance(_CRAWL4AI_AVAILABLE, bool)

    def test_ddgs_flag_exists(self):
        from scout.enricher import _DDGS_AVAILABLE
        assert isinstance(_DDGS_AVAILABLE, bool)

    def test_skip_link_keys_defined(self):
        from scout.enricher import _SKIP_LINK_KEYS
        assert "website" in _SKIP_LINK_KEYS
        assert "github_urls" in _SKIP_LINK_KEYS
        assert "directory_source_urls" in _SKIP_LINK_KEYS

    def test_platform_patterns_defined(self):
        from scout.enricher import _PLATFORM_PATTERNS
        assert "linkedin" in _PLATFORM_PATTERNS
        assert "huggingface" in _PLATFORM_PATTERNS
        assert "crunchbase" in _PLATFORM_PATTERNS
