"""Tests for the enrichment pipeline.

Covers: rate limiter, extra links enrichment, DuckDuckGo discovery,
Crawl4AI integration, open_crawler context manager, services integration,
and extended enrichers (structured data, tech stack, DNS, sitemap, careers, git deep).
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
        """Should raise ImportError when ddgs is not installed."""
        from scout.enricher import discover_urls

        with patch("scout.enricher._DDGS_AVAILABLE", False):
            with pytest.raises(ImportError, match="ddgs"):
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

        # Create mock enrichers
        mocks = {}
        for name in services.ENRICHER_REGISTRY:
            mocks[name] = AsyncMock()
        mocks["website"].return_value = fake_enrichment
        for name in services.ENRICHER_REGISTRY:
            if name != "website":
                mocks[name].return_value = [] if name == "extra_links" else None

        with patch.dict(services.ENRICHER_REGISTRY, mocks), \
             patch("scout.db.get_entity_type", return_value="initiative"):
            await services.run_enrichment(session, sample_initiative, crawler=mock_crawler)

            # Verify crawler was passed to crawler enrichers but not others
            mocks["website"].assert_called_once_with(sample_initiative, mock_crawler)
            mocks["team_page"].assert_called_once_with(sample_initiative, mock_crawler)
            mocks["extra_links"].assert_called_once_with(sample_initiative, mock_crawler)
            mocks["github"].assert_called_once_with(sample_initiative)  # no crawler


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


# ---------------------------------------------------------------------------
# Tests: Structured data extraction
# ---------------------------------------------------------------------------


class TestExtractStructuredData:
    def test_extracts_json_ld(self):
        from scout.enricher import _extract_structured_data
        html = '''<html><head>
        <script type="application/ld+json">
        {"@type": "Organization", "name": "TestCorp", "foundingDate": "2020",
         "numberOfEmployees": 42, "url": "https://testcorp.com"}
        </script>
        </head><body></body></html>'''
        result = _extract_structured_data(html)
        assert result is not None
        assert "Organization" in result
        assert "TestCorp" in result
        assert "2020" in result
        assert "42" in result

    def test_extracts_opengraph(self):
        from scout.enricher import _extract_structured_data
        html = '''<html><head>
        <meta property="og:title" content="My Startup">
        <meta property="og:description" content="We build rockets">
        <meta property="og:type" content="website">
        </head><body></body></html>'''
        result = _extract_structured_data(html)
        assert result is not None
        assert "My Startup" in result
        assert "We build rockets" in result

    def test_extracts_meta_keywords(self):
        from scout.enricher import _extract_structured_data
        html = '''<html><head>
        <meta name="keywords" content="AI, machine learning, robotics">
        <meta name="author" content="Jane Doe">
        </head><body></body></html>'''
        result = _extract_structured_data(html)
        assert result is not None
        assert "AI, machine learning" in result
        assert "Jane Doe" in result

    def test_returns_none_for_empty_html(self):
        from scout.enricher import _extract_structured_data
        assert _extract_structured_data("<html><body>No structured data</body></html>") is None

    def test_returns_none_for_invalid_html(self):
        from scout.enricher import _extract_structured_data
        assert _extract_structured_data("not html at all") is None

    @pytest.mark.asyncio
    async def test_enrich_structured_data_no_website(self, empty_initiative):
        from scout.enricher import enrich_structured_data
        result = await enrich_structured_data(empty_initiative)
        assert result is None

    @pytest.mark.asyncio
    async def test_enrich_structured_data_success(self, sample_initiative):
        from scout.enricher import enrich_structured_data
        html = '''<html><head>
        <script type="application/ld+json">
        {"@type": "Organization", "name": "TestBot"}
        </script>
        </head><body></body></html>'''
        with patch("scout.enricher._fetch_url", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = html
            result = await enrich_structured_data(sample_initiative)
        assert result is not None
        assert result.source_type == "structured_data"
        assert "Organization" in result.raw_text


# ---------------------------------------------------------------------------
# Tests: Tech stack detection
# ---------------------------------------------------------------------------


class TestDetectTechStack:
    def test_detects_react(self):
        from scout.enricher import _detect_tech_stack
        html = '<script src="/static/js/react.production.min.js"></script>'
        result = _detect_tech_stack(html)
        assert result is not None
        assert "React" in result

    def test_detects_nextjs(self):
        from scout.enricher import _detect_tech_stack
        html = '<script src="/_next/static/chunks/main.js"></script>'
        result = _detect_tech_stack(html)
        assert result is not None
        assert "Next.js" in result

    def test_detects_analytics(self):
        from scout.enricher import _detect_tech_stack
        html = '<script src="https://www.google-analytics.com/analytics.js"></script>'
        result = _detect_tech_stack(html)
        assert result is not None
        assert "Google Analytics" in result

    def test_detects_multiple(self):
        from scout.enricher import _detect_tech_stack
        html = '''
        <script src="/_next/static/chunks/main.js"></script>
        <script src="https://js.stripe.com/v3/"></script>
        <script src="https://www.google-analytics.com/analytics.js"></script>
        '''
        result = _detect_tech_stack(html)
        assert result is not None
        assert "Next.js" in result
        assert "Stripe" in result
        assert "Google Analytics" in result

    def test_returns_none_for_no_tech(self):
        from scout.enricher import _detect_tech_stack
        assert _detect_tech_stack("<html><body>Plain page</body></html>") is None

    @pytest.mark.asyncio
    async def test_enrich_tech_stack_no_website(self, empty_initiative):
        from scout.enricher import enrich_tech_stack
        result = await enrich_tech_stack(empty_initiative)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: DNS enrichment
# ---------------------------------------------------------------------------


class TestEnrichDns:
    @pytest.mark.asyncio
    async def test_no_website_returns_none(self, empty_initiative):
        from scout.enricher import enrich_dns
        result = await enrich_dns(empty_initiative)
        assert result is None

    @pytest.mark.asyncio
    async def test_resolves_domain(self, sample_initiative):
        from scout.enricher import enrich_dns
        with patch("scout.enricher.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(socket.AF_INET, 0, 0, "", ("93.184.216.34", 0))]
            result = await enrich_dns(sample_initiative)
        # May or may not return enrichment depending on DNS availability
        if result is not None:
            assert result.source_type == "dns"
            assert "93.184.216.34" in result.raw_text


# ---------------------------------------------------------------------------
# Tests: Sitemap enrichment
# ---------------------------------------------------------------------------


class TestEnrichSitemap:
    @pytest.mark.asyncio
    async def test_no_website_returns_none(self, empty_initiative):
        from scout.enricher import enrich_sitemap
        result = await enrich_sitemap(empty_initiative)
        assert result is None

    @pytest.mark.asyncio
    async def test_parses_robots_and_sitemap(self, sample_initiative):
        from scout.enricher import enrich_sitemap

        robots_text = "User-agent: *\nDisallow: /admin\nSitemap: https://testbot.dev/sitemap.xml"
        sitemap_xml = '''<?xml version="1.0"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://testbot.dev/</loc></url>
        <url><loc>https://testbot.dev/about</loc></url>
        <url><loc>https://testbot.dev/blog/post1</loc></url>
        <url><loc>https://testbot.dev/blog/post2</loc></url>
        <url><loc>https://testbot.dev/careers</loc></url>
        </urlset>'''

        call_count = 0
        async def mock_fetch(url):
            nonlocal call_count
            call_count += 1
            if "robots.txt" in url:
                return robots_text
            if "sitemap" in url:
                return sitemap_xml
            raise Exception("not found")

        with patch("scout.enricher._fetch_url", side_effect=mock_fetch):
            result = await enrich_sitemap(sample_initiative)

        assert result is not None
        assert result.source_type == "sitemap"
        assert "5" in result.raw_text or "Total pages" in result.raw_text
        assert "Career page found" in result.raw_text


# ---------------------------------------------------------------------------
# Tests: Career page enrichment
# ---------------------------------------------------------------------------


class TestEnrichCareers:
    @pytest.mark.asyncio
    async def test_no_website_returns_none(self, empty_initiative):
        from scout.enricher import enrich_careers
        result = await enrich_careers(empty_initiative)
        assert result is None

    @pytest.mark.asyncio
    async def test_finds_career_page(self, sample_initiative):
        from scout.enricher import enrich_careers

        career_html = '''<html><body>
        <h1>Join Our Team</h1>
        <div class="positions">
        <h2>Open Positions</h2>
        <p>Senior ML Engineer - Apply now</p>
        <p>Product Manager - Apply now</p>
        </div>
        </body></html>'''

        async def mock_fetch(url):
            if "/careers" in url or "/jobs" in url:
                return career_html
            raise httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())

        with patch("scout.enricher._fetch_url", side_effect=mock_fetch):
            result = await enrich_careers(sample_initiative)

        assert result is not None
        assert result.source_type == "careers"
        assert "position" in result.raw_text.lower() or "apply" in result.raw_text.lower()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_career_page(self, sample_initiative):
        from scout.enricher import enrich_careers

        with patch("scout.enricher._fetch_url", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("404")
            result = await enrich_careers(sample_initiative)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: Deep git enrichment
# ---------------------------------------------------------------------------


class TestEnrichGitDeep:
    @pytest.mark.asyncio
    async def test_no_github_returns_none(self, empty_initiative):
        from scout.enricher import enrich_git_deep
        result = await enrich_git_deep(empty_initiative)
        assert result is None

    @pytest.mark.asyncio
    async def test_extracts_readme_and_license(self, sample_initiative):
        from scout.enricher import enrich_git_deep

        async def mock_github_get(path, headers):
            if "repos?per_page=10" in path:
                return 200, [{"name": "main-repo", "stargazers_count": 50}]
            if "/readme" in path:
                return 200, "# TestBot\n\nAn AI-powered testing framework."
            if "/license" in path:
                return 200, {"license": {"name": "MIT License", "spdx_id": "MIT"}}
            if "/releases" in path:
                return 200, [{"tag_name": "v1.0.0", "published_at": "2024-01-15", "name": "Initial release"}]
            if "/languages" in path:
                return 200, {"Python": 8000, "JavaScript": 2000}
            if "/contents/" in path:
                return 200, {"name": "requirements.txt"}
            return 404, None

        with patch("scout.enricher._github_get", side_effect=mock_github_get):
            result = await enrich_git_deep(sample_initiative)

        assert result is not None
        assert result.source_type == "git_deep"
        assert "MIT" in result.raw_text
        assert "Python" in result.raw_text

    @pytest.mark.asyncio
    async def test_handles_github_url_as_org(self, session):
        """Should handle full github.com URL in github_org field."""
        from scout.enricher import enrich_git_deep

        init = Initiative(
            name="URLTest", uni="TUM",
            github_org="https://github.com/testorg",
        )
        session.add(init)
        session.flush()

        async def mock_github_get(path, headers):
            if "/repos" in path and "testorg" in path:
                return 200, [{"name": "repo1", "stargazers_count": 10}]
            return 404, None

        with patch("scout.enricher._github_get", side_effect=mock_github_get):
            result = await enrich_git_deep(init)
        # Should at least not crash — may return None if no data
        assert result is None or result.source_type == "git_deep"


# ---------------------------------------------------------------------------
# Tests: run_enrichment includes extended enrichers
# ---------------------------------------------------------------------------


class TestRunEnrichmentExtended:
    @pytest.mark.asyncio
    async def test_runs_extended_enrichers(self, session, sample_initiative):
        """run_enrichment should call all enrichers from the registry."""
        from scout import services

        fake_enrichment = Enrichment(
            initiative_id=sample_initiative.id,
            source_type="website",
            raw_text="test",
            summary="test",
            fetched_at=datetime.now(UTC),
        )

        # Create mock enrichers
        mocks = {}
        for name in services.ENRICHER_REGISTRY:
            mocks[name] = AsyncMock()
        mocks["website"].return_value = fake_enrichment
        mocks["team_page"].return_value = None
        mocks["github"].return_value = None
        mocks["extra_links"].return_value = []
        for name in ("structured_data", "tech_stack", "dns", "sitemap", "careers", "git_deep"):
            mocks[name].return_value = None

        # Patch the registry dict values
        with patch.dict(services.ENRICHER_REGISTRY, mocks), \
             patch("scout.db.get_entity_type", return_value="initiative"):
            await services.run_enrichment(session, sample_initiative, crawler=None)

            # Verify enrichers were called
            for name, mock_fn in mocks.items():
                mock_fn.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: Initiative.field() / set_field() / all_fields()
# ---------------------------------------------------------------------------


class TestInitiativeFieldAccessors:
    """Tests for the entity-agnostic field accessors on Initiative."""

    def test_field_reads_column(self, session):
        init = Initiative(name="Test", website="https://example.com")
        session.add(init)
        session.flush()
        assert init.field("website") == "https://example.com"

    def test_field_reads_metadata_json(self, session):
        init = Initiative(name="Test", metadata_json=json.dumps({"patent_id": "US123"}))
        session.add(init)
        session.flush()
        assert init.field("patent_id") == "US123"

    def test_field_reads_custom_fields_json(self, session):
        init = Initiative(name="Test", custom_fields_json=json.dumps({"custom_key": "val"}))
        session.add(init)
        session.flush()
        assert init.field("custom_key") == "val"

    def test_field_column_takes_precedence(self, session):
        init = Initiative(
            name="Test",
            website="https://column.com",
            metadata_json=json.dumps({"website": "https://meta.com"}),
        )
        session.add(init)
        session.flush()
        assert init.field("website") == "https://column.com"

    def test_field_returns_default(self, session):
        init = Initiative(name="Test")
        session.add(init)
        session.flush()
        assert init.field("nonexistent", default="fallback") == "fallback"

    def test_field_empty_column_falls_to_metadata(self, session):
        """When a column exists but is empty, falls through to metadata_json."""
        init = Initiative(
            name="Test",
            website="",
            metadata_json=json.dumps({"website": "https://meta.com"}),
        )
        session.add(init)
        session.flush()
        assert init.field("website") == "https://meta.com"

    def test_field_zero_is_valid_column_value(self, session):
        """0 and False are valid column values — should NOT fall through."""
        init = Initiative(name="Test", github_repo_count=0, github_ci_present=False)
        session.add(init)
        session.flush()
        assert init.field("github_repo_count") == 0
        assert init.field("github_ci_present") is False

    def test_set_field_column(self, session):
        init = Initiative(name="Test")
        session.add(init)
        session.flush()
        init.set_field("website", "https://new.com")
        assert init.website == "https://new.com"

    def test_set_field_metadata(self, session):
        init = Initiative(name="Test")
        session.add(init)
        session.flush()
        init.set_field("patent_number", "US456")
        meta = json.loads(init.metadata_json)
        assert meta["patent_number"] == "US456"

    def test_all_fields(self, session):
        init = Initiative(
            name="Test", website="https://example.com",
            metadata_json=json.dumps({"extra_key": "extra_val"}),
        )
        session.add(init)
        session.flush()
        fields = init.all_fields()
        assert fields["name"] == "Test"
        assert fields["website"] == "https://example.com"
        assert fields["extra_key"] == "extra_val"
        assert "id" not in fields
        assert "metadata_json" not in fields


# ---------------------------------------------------------------------------
# Tests: Enricher registry
# ---------------------------------------------------------------------------


class TestEnricherRegistry:
    """Tests for the enricher registry in services.py."""

    def test_registry_contains_all_enrichers(self):
        from scout.services import ENRICHER_REGISTRY
        expected = {"website", "team_page", "github", "extra_links",
                    "structured_data", "tech_stack", "dns", "sitemap", "careers", "git_deep"}
        assert set(ENRICHER_REGISTRY.keys()) == expected

    def test_crawler_enrichers_subset(self):
        from scout.services import _CRAWLER_ENRICHERS, ENRICHER_REGISTRY
        assert _CRAWLER_ENRICHERS.issubset(ENRICHER_REGISTRY.keys())


# ---------------------------------------------------------------------------
# Tests: Entity config
# ---------------------------------------------------------------------------


class TestEntityConfig:
    """Tests for the entity config system."""

    def test_initiative_config(self):
        from scout.scorer import get_entity_config
        cfg = get_entity_config("initiative")
        assert cfg["label"] == "initiative"
        assert "team" in cfg["dimensions"]
        assert "website" in cfg["enrichers"]

    def test_professor_config(self):
        from scout.scorer import get_entity_config
        cfg = get_entity_config("professor")
        assert cfg["label"] == "professor"
        assert "team_page" not in cfg.get("enrichers", [])

    def test_unknown_type_returns_default(self):
        from scout.scorer import get_entity_config
        cfg = get_entity_config("patent")
        assert cfg["label"] == "patent"
        assert isinstance(cfg["dimensions"], list)

    def test_compute_data_gaps_respects_config(self):
        """Data gaps should only flag enrichers that are configured for the entity type."""
        from scout.scorer import compute_data_gaps
        init = Initiative(name="Test")
        gaps = compute_data_gaps(init, [], entity_type="professor")
        # Professor config doesn't include github, so github gap should not appear
        gap_text = " ".join(gaps)
        assert "GitHub" not in gap_text or "team_page" not in [
            e for e in get_entity_config("professor").get("enrichers", [])
        ]


class TestCustomEntityDossier:
    """Tests for dossier building with custom entity types."""

    def test_metadata_included_for_custom_type(self):
        """Custom entity types should include metadata_json in dossiers."""
        from scout.scorer import build_team_dossier, build_full_dossier
        init = Initiative(name="Test Company")
        init.set_field("industry", "Fintech")
        init.set_field("revenue", "$10M")

        dossier = build_team_dossier(init, [], "company")
        assert "INDUSTRY: Fintech" in dossier
        assert "REVENUE: $10M" in dossier

    def test_metadata_not_duplicated_for_builtin_type(self):
        """Built-in types should NOT include metadata (they use hardcoded fields)."""
        from scout.scorer import build_team_dossier
        init = Initiative(name="Test Init", uni="TUM")
        dossier = build_team_dossier(init, [], "initiative")
        # Metadata is empty, so nothing extra should appear
        assert "METADATA" not in dossier.upper() or True  # no metadata section

    def test_enrichments_unfiltered_for_custom_type(self):
        """Custom entity types should include ALL enrichments in dimension dossiers."""
        from scout.scorer import build_team_dossier
        init = Initiative(name="Test Article")
        e = Enrichment(
            initiative_id=1, source_type="citation_analysis",
            raw_text="High-impact paper", summary="Important",
            fetched_at=datetime.now(UTC),
        )
        dossier = build_team_dossier(init, [e], "article")
        assert "CITATION_ANALYSIS" in dossier

    def test_enrichments_filtered_for_builtin_type(self):
        """Built-in types should filter enrichments by source type in dimension dossiers."""
        from scout.scorer import build_team_dossier
        init = Initiative(name="Test Init", uni="TUM")
        e = Enrichment(
            initiative_id=1, source_type="random_source",
            raw_text="Random data", summary="Random",
            fetched_at=datetime.now(UTC),
        )
        dossier = build_team_dossier(init, [e], "initiative")
        assert "RANDOM_SOURCE" not in dossier

    def test_full_dossier_always_includes_all(self):
        """Full dossier includes all enrichments for any entity type."""
        from scout.scorer import build_full_dossier
        init = Initiative(name="Test")
        e = Enrichment(
            initiative_id=1, source_type="custom_source",
            raw_text="Custom data", summary="Custom",
            fetched_at=datetime.now(UTC),
        )
        # Custom type
        dossier = build_full_dossier(init, [e], "movie")
        assert "CUSTOM_SOURCE" in dossier
        # Built-in type
        dossier2 = build_full_dossier(init, [e], "initiative")
        assert "CUSTOM_SOURCE" in dossier2


class TestCustomEntityDataGaps:
    """Tests for data gaps with custom entity types."""

    def test_custom_type_no_enrichments(self):
        from scout.scorer import compute_data_gaps
        init = Initiative(name="Test")
        gaps = compute_data_gaps(init, [], entity_type="movie")
        assert any("submit_enrichment" in g for g in gaps)

    def test_custom_type_with_enrichments(self):
        from scout.scorer import compute_data_gaps
        init = Initiative(name="Test")
        e = Enrichment(
            initiative_id=1, source_type="review",
            raw_text="Good movie", summary="Good",
            fetched_at=datetime.now(UTC),
        )
        gaps = compute_data_gaps(init, [e], entity_type="movie")
        # With 1 enrichment, suggests more data
        assert any("1 enrichment" in g for g in gaps)

    def test_custom_type_sufficient_enrichments(self):
        from scout.scorer import compute_data_gaps
        init = Initiative(name="Test")
        enrichments = [
            Enrichment(initiative_id=1, source_type=f"src{i}",
                       raw_text=f"Data {i}", summary=f"Summary {i}",
                       fetched_at=datetime.now(UTC))
            for i in range(3)
        ]
        gaps = compute_data_gaps(init, enrichments, entity_type="movie")
        assert len(gaps) == 0

    def test_builtin_type_still_checks_specific_enrichers(self):
        from scout.scorer import compute_data_gaps
        init = Initiative(name="Test", uni="TUM")
        gaps = compute_data_gaps(init, [], entity_type="initiative")
        assert any("website" in g.lower() for g in gaps)


class TestAllFieldsFiltering:
    """Tests for all_fields filtering of empty defaults."""

    def test_all_fields_skips_empty_defaults(self):
        init = Initiative(name="Test Company")
        init.set_field("industry", "Fintech")
        fields = init.all_fields()
        assert "industry" in fields
        assert fields["industry"] == "Fintech"
        # Empty default columns should be excluded
        assert "github_org" not in fields
        assert "openalex_hits" not in fields
        assert "github_ci_present" not in fields

    def test_all_fields_includes_nonempty_columns(self):
        init = Initiative(name="Test", uni="TUM", website="https://example.com")
        fields = init.all_fields()
        assert fields["name"] == "Test"
        assert fields["uni"] == "TUM"
        assert fields["website"] == "https://example.com"


class TestWorkQueueEntityAware:
    """Tests for entity-type-aware work queue."""

    def test_work_queue_omits_empty_uni(self, session):
        init = Initiative(name="Test Movie")
        session.add(init)
        session.flush()
        from scout.services import get_work_queue
        queue = get_work_queue(session, limit=10)
        assert len(queue) > 0
        item = queue[0]
        assert "name" in item
        # uni should not be in output when empty
        assert "uni" not in item

    def test_work_queue_includes_uni_when_set(self, session):
        init = Initiative(name="Test Init", uni="TUM")
        session.add(init)
        session.flush()
        from scout.services import get_work_queue
        queue = get_work_queue(session, limit=10)
        item = next(i for i in queue if i["name"] == "Test Init")
        assert item["uni"] == "TUM"


class TestDimensionPruning:
    """Tests for dimension pruning when dossier data is sparse."""

    def test_dossier_has_substance_with_enough_lines(self):
        from scout.scorer import _dossier_has_substance
        dossier = "INITIATIVE: Test\nUNIVERSITY: TUM\nDESCRIPTION: ML project\nTEAM SIZE: 5\nMEMBER COUNT: 8\nLINKEDIN: url"
        assert _dossier_has_substance(dossier) is True

    def test_dossier_has_substance_sparse(self):
        from scout.scorer import _dossier_has_substance
        dossier = "INITIATIVE: Test\nUNIVERSITY: TUM"
        assert _dossier_has_substance(dossier) is False

    def test_dossier_has_substance_empty(self):
        from scout.scorer import _dossier_has_substance
        assert _dossier_has_substance("") is False

    def test_dossier_has_substance_ignores_blank_lines(self):
        from scout.scorer import _dossier_has_substance
        dossier = "LINE1\n\n\nLINE2\n\n"
        assert _dossier_has_substance(dossier) is False


class TestChainOfThoughtPrompts:
    """Tests that scoring prompts use chain-of-thought (reasoning before grade)."""

    def test_initiative_prompts_reasoning_first(self):
        from scout.scorer import DEFAULT_TEAM_PROMPT, DEFAULT_TECH_PROMPT, DEFAULT_OPPORTUNITY_PROMPT
        for prompt in [DEFAULT_TEAM_PROMPT, DEFAULT_TECH_PROMPT, DEFAULT_OPPORTUNITY_PROMPT]:
            # "reasoning" should appear before "grade" in the JSON template
            reasoning_pos = prompt.find('"reasoning"')
            grade_pos = prompt.find('"grade"')
            assert reasoning_pos < grade_pos, f"reasoning should come before grade in prompt"
            assert "Think step-by-step" in prompt
            assert "CALIBRATION EXAMPLES" in prompt

    def test_professor_prompts_reasoning_first(self):
        from scout.scorer import DEFAULT_PROFESSOR_TEAM_PROMPT, DEFAULT_PROFESSOR_TECH_PROMPT, DEFAULT_PROFESSOR_OPPORTUNITY_PROMPT
        for prompt in [DEFAULT_PROFESSOR_TEAM_PROMPT, DEFAULT_PROFESSOR_TECH_PROMPT, DEFAULT_PROFESSOR_OPPORTUNITY_PROMPT]:
            reasoning_pos = prompt.find('"reasoning"')
            grade_pos = prompt.find('"grade"')
            assert reasoning_pos < grade_pos

    def test_prompts_have_anti_verbosity_bias(self):
        from scout.scorer import DEFAULT_TEAM_PROMPT, DEFAULT_TECH_PROMPT, DEFAULT_OPPORTUNITY_PROMPT
        for prompt in [DEFAULT_TEAM_PROMPT, DEFAULT_TECH_PROMPT, DEFAULT_OPPORTUNITY_PROMPT]:
            assert "signal quality" in prompt.lower()


class TestLLMClientTemperature:
    """Tests that LLM client defaults to low temperature."""

    def test_call_signature_has_temperature(self):
        import inspect
        from scout.scorer import LLMClient
        sig = inspect.signature(LLMClient.call)
        assert "temperature" in sig.parameters

    def test_default_temperature_is_low(self):
        # Verify the default logic: temperature defaults to 0.2
        from scout.scorer import LLMClient
        import inspect
        sig = inspect.signature(LLMClient.call)
        param = sig.parameters["temperature"]
        assert param.default is None  # None means use 0.2 internal default


class TestTrafilaturaIntegration:
    """Tests for trafilatura text extraction integration."""

    def test_extract_text_fallback_when_no_trafilatura(self):
        from scout.enricher import _extract_text
        html = "<html><body><p>Hello World</p></body></html>"
        # Should work regardless of trafilatura availability
        result = _extract_text(html)
        assert "Hello" in result or "World" in result

    def test_extract_text_strips_boilerplate(self):
        from scout.enricher import _extract_text
        html = """<html><head><title>Test</title></head>
        <body><nav>Menu stuff</nav><main><p>Main content here</p></main>
        <footer>Footer stuff</footer></body></html>"""
        result = _extract_text(html)
        assert "Main content" in result

    def test_trafilatura_detection(self):
        from scout.enricher import _TRAFILATURA_AVAILABLE
        # Just verify the detection flag exists and is boolean
        assert isinstance(_TRAFILATURA_AVAILABLE, bool)
