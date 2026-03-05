"""Metadata enrichers: structured data, tech stack, DNS, sitemap."""
from __future__ import annotations

import asyncio
import json as json_mod
import logging
import re
import socket
from urllib.parse import urlparse

from scout.enricher._core import (
    _EXTRUCT_AVAILABLE,
    _fetch_url,
    _get_website_url,
    _make_enrichment,
    _parse_html,
    extruct,
)
from scout.models import Enrichment, Initiative

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Structured data extraction (JSON-LD, OpenGraph, meta tags)
# ---------------------------------------------------------------------------

_JSONLD_KEYS = (
    "name", "description", "url", "foundingDate",
    "numberOfEmployees", "address", "sameAs",
    "founder", "email", "telephone", "logo",
    "areaServed", "knowsAbout", "memberOf",
    "author", "datePublished", "publisher",
    "genre", "director", "actor", "brand",
    "offers", "aggregateRating",
)


def _format_jsonld_value(val) -> str:
    """Format a JSON-LD value for display."""
    if isinstance(val, list):
        return ", ".join(str(v)[:100] for v in val[:5])
    if isinstance(val, dict):
        return val.get("name") or val.get("value") or str(val)[:200]
    return str(val)[:300]


def _extract_structured_data(raw_html: str) -> str | None:
    """Extract JSON-LD, OpenGraph, microdata, and meta tags from HTML."""
    if _EXTRUCT_AVAILABLE:
        try:
            data = extruct.extract(raw_html, syntaxes=["json-ld", "opengraph", "microdata"])
            lines: list[str] = []

            for item in (data.get("json-ld") or [])[:5]:
                if not isinstance(item, dict):
                    continue
                ld_type = item.get("@type", "")
                if ld_type:
                    lines.append(f"Schema.org type: {ld_type}")
                for key in _JSONLD_KEYS:
                    val = item.get(key)
                    if val:
                        lines.append(f"  {key}: {_format_jsonld_value(val)}")

            for og in (data.get("opengraph") or [])[:3]:
                if not isinstance(og, dict):
                    continue
                for prop in og.get("properties", []):
                    if isinstance(prop, (list, tuple)) and len(prop) == 2:
                        name, content = prop
                        if content:
                            lines.append(f"OG {name}: {str(content)[:300]}")

            for md_item in (data.get("microdata") or [])[:3]:
                if not isinstance(md_item, dict):
                    continue
                md_type = md_item.get("type", "")
                if md_type:
                    lines.append(f"Microdata type: {md_type}")
                for key, val in (md_item.get("properties") or {}).items():
                    if val:
                        lines.append(f"  {key}: {str(val)[:300]}")

            if lines:
                return "\n".join(lines)
        except Exception:
            pass

    # Fallback: manual lxml-based extraction
    tree = _parse_html(raw_html)
    if tree is None:
        return None

    lines = []

    for script in tree.xpath('//script[@type="application/ld+json"]'):
        text = (script.text or "").strip()
        if not text:
            continue
        try:
            data = json_mod.loads(text)
            items = data if isinstance(data, list) else [data]
            for item in items[:3]:
                if not isinstance(item, dict):
                    continue
                ld_type = item.get("@type", "")
                if ld_type:
                    lines.append(f"Schema.org type: {ld_type}")
                for key in _JSONLD_KEYS:
                    val = item.get(key)
                    if val:
                        lines.append(f"  {key}: {_format_jsonld_value(val)}")
        except (json_mod.JSONDecodeError, TypeError):
            continue

    og_tags = tree.xpath('//meta[starts-with(@property, "og:")]')
    for tag in og_tags:
        prop = (tag.get("property") or "")[3:]
        content = (tag.get("content") or "").strip()
        if prop and content:
            lines.append(f"OG {prop}: {content[:300]}")

    tw_tags = tree.xpath('//meta[starts-with(@name, "twitter:")]')
    for tag in tw_tags:
        name = (tag.get("name") or "")[8:]
        content = (tag.get("content") or "").strip()
        if name and content and name not in ("card",):
            lines.append(f"Twitter {name}: {content[:300]}")

    for meta_name in ("author", "keywords", "generator", "geo.region",
                      "geo.placename", "geo.position"):
        vals = tree.xpath(f'//meta[@name="{meta_name}"]/@content')
        for val in vals:
            if val and val.strip():
                lines.append(f"Meta {meta_name}: {val.strip()[:200]}")

    return "\n".join(lines) if lines else None


async def enrich_structured_data(initiative: Initiative) -> Enrichment | None:
    """Extract JSON-LD, OpenGraph, and meta tags from the initiative's website."""
    url = _get_website_url(initiative)
    if not url:
        return None

    try:
        raw_html = await _fetch_url(url)
    except Exception as exc:
        log.warning("Structured data fetch failed for %s: %s", url, exc)
        return None

    text = _extract_structured_data(raw_html)
    if not text:
        return None

    return _make_enrichment(initiative, "structured_data", url, text)


# ---------------------------------------------------------------------------
# Technology stack detection
# ---------------------------------------------------------------------------

_TECH_FINGERPRINTS: list[tuple[str, str, re.Pattern]] = [
    (cat, name, re.compile(pattern, re.IGNORECASE))
    for cat, name, pattern in [
        ("framework", "React", r'react(?:\.production|\.development|dom)'),
        ("framework", "Next.js", r'(?:_next/static|__next|next/dist)'),
        ("framework", "Vue.js", r'(?:vue\.(?:min\.)?js|__vue__|v-cloak)'),
        ("framework", "Nuxt.js", r'(?:_nuxt/|__nuxt)'),
        ("framework", "Angular", r'(?:ng-version|angular(?:\.min)?\.js)'),
        ("framework", "Svelte", r'(?:svelte-[\w]+|__svelte)'),
        ("framework", "WordPress", r'(?:wp-content|wp-includes|wordpress)'),
        ("framework", "Shopify", r'(?:cdn\.shopify\.com|Shopify\.theme)'),
        ("framework", "Webflow", r'(?:webflow\.com|wf-page)'),
        ("framework", "Wix", r'(?:wix\.com|wixstatic\.com)'),
        ("framework", "Squarespace", r'(?:squarespace\.com|sqsp)'),
        ("framework", "Ghost", r'(?:ghost\.(?:io|org)|ghost-(?:url|api))'),
        ("framework", "Hugo", r'(?:gohugo\.io|powered.*hugo)'),
        ("framework", "Gatsby", r'gatsby'),
        ("framework", "Django", r'(?:csrfmiddlewaretoken|django)'),
        ("framework", "Ruby on Rails", r'(?:csrf-token.*authenticity|rails-ujs)'),
        ("framework", "Laravel", r'(?:laravel|XSRF-TOKEN)'),
        ("analytics", "Google Analytics", r'(?:google-analytics\.com|gtag|googletagmanager)'),
        ("analytics", "Plausible", r'plausible\.io'),
        ("analytics", "Matomo", r'(?:matomo|piwik)'),
        ("analytics", "Mixpanel", r'mixpanel'),
        ("analytics", "Hotjar", r'hotjar'),
        ("analytics", "PostHog", r'posthog'),
        ("marketing", "HubSpot", r'(?:hubspot|hs-scripts|hbspt)'),
        ("marketing", "Intercom", r'(?:intercom|intercomSettings)'),
        ("marketing", "Drift", r'drift\.com'),
        ("marketing", "Crisp", r'crisp\.chat'),
        ("marketing", "Mailchimp", r'mailchimp'),
        ("marketing", "Typeform", r'typeform'),
        ("payments", "Stripe", r'(?:stripe\.com/v|Stripe\()'),
        ("payments", "PayPal", r'paypal'),
        ("infrastructure", "Cloudflare", r'(?:cloudflare|cf-ray)'),
        ("infrastructure", "Vercel", r'(?:vercel|\.vercel\.app)'),
        ("infrastructure", "Netlify", r'(?:netlify)'),
        ("infrastructure", "Heroku", r'heroku'),
        ("infrastructure", "Firebase", r'(?:firebase|firebaseapp)'),
    ]
]


def _detect_tech_stack(raw_html: str) -> str | None:
    """Detect technologies from HTML source code fingerprints."""
    if not raw_html:
        return None

    found: dict[str, list[str]] = {}
    for category, name, pattern in _TECH_FINGERPRINTS:
        if pattern.search(raw_html):
            found.setdefault(category, []).append(name)

    if not found:
        return None

    lines: list[str] = ["DETECTED TECHNOLOGY STACK:"]
    for category, names in sorted(found.items()):
        lines.append(f"  {category}: {', '.join(names)}")

    return "\n".join(lines)


async def enrich_tech_stack(initiative: Initiative) -> Enrichment | None:
    """Detect the technology stack from the initiative's website HTML."""
    url = _get_website_url(initiative)
    if not url:
        return None

    try:
        raw_html = await _fetch_url(url)
    except Exception as exc:
        log.warning("Tech stack detection failed for %s: %s", url, exc)
        return None

    text = _detect_tech_stack(raw_html)
    if not text:
        return None

    return _make_enrichment(initiative, "tech_stack", url, text)


# ---------------------------------------------------------------------------
# DNS enrichment
# ---------------------------------------------------------------------------


async def _dns_lookup(domain: str) -> str | None:
    """Perform DNS lookups for MX and TXT records."""
    lines: list[str] = [f"DNS ENRICHMENT: {domain}"]

    try:
        try:
            addrs = await asyncio.to_thread(socket.getaddrinfo, domain, None, socket.AF_INET)
            if addrs:
                ips = {a[4][0] for a in addrs}
                lines.append(f"  Resolves to: {', '.join(sorted(ips)[:3])}")
        except socket.gaierror:
            lines.append("  Domain does not resolve (no A record)")
            return "\n".join(lines) if len(lines) > 1 else None

        try:
            import dns.resolver  # type: ignore[import-untyped]
            mx_records = await asyncio.to_thread(
                lambda: list(dns.resolver.resolve(domain, "MX"))
            )
            mx_hosts = [str(r.exchange).rstrip(".").lower() for r in mx_records]
            lines.append(f"  MX records: {', '.join(mx_hosts[:5])}")
            mx_str = " ".join(mx_hosts)
            if "google" in mx_str or "gmail" in mx_str:
                lines.append("  Email provider: Google Workspace")
            elif "outlook" in mx_str or "microsoft" in mx_str:
                lines.append("  Email provider: Microsoft 365")
            elif "zoho" in mx_str:
                lines.append("  Email provider: Zoho Mail")
            elif "protonmail" in mx_str or "proton" in mx_str:
                lines.append("  Email provider: ProtonMail")
        except ImportError:
            pass
        except Exception:
            pass

        try:
            import dns.resolver  # type: ignore[import-untyped]
            txt_records = await asyncio.to_thread(
                lambda: list(dns.resolver.resolve(domain, "TXT"))
            )
            for rdata in txt_records[:10]:
                txt = str(rdata).strip('"')
                if txt.startswith("v=spf"):
                    lines.append(f"  SPF: {txt[:200]}")
                elif "google-site-verification" in txt:
                    lines.append("  Verified: Google Search Console")
                elif "facebook-domain-verification" in txt:
                    lines.append("  Verified: Facebook/Meta")
                elif "MS=" in txt:
                    lines.append("  Verified: Microsoft")
                elif "_dmarc" in txt or "v=DMARC" in txt.upper():
                    lines.append("  DMARC: configured")
        except ImportError:
            pass
        except Exception:
            pass

    except Exception as exc:
        log.debug("DNS lookup failed for %s: %s", domain, exc)
        return None

    return "\n".join(lines) if len(lines) > 1 else None


async def enrich_dns(initiative: Initiative) -> Enrichment | None:
    """Look up DNS records (MX, TXT) for the initiative's domain."""
    url = _get_website_url(initiative)
    if not url:
        return None
    domain = urlparse(url).netloc
    if not domain:
        return None
    if domain.startswith("www."):
        domain = domain[4:]

    text = await _dns_lookup(domain)
    if not text:
        return None

    return _make_enrichment(initiative, "dns", url, text)


# ---------------------------------------------------------------------------
# Sitemap / robots.txt enrichment
# ---------------------------------------------------------------------------


async def enrich_sitemap(initiative: Initiative) -> Enrichment | None:
    """Parse robots.txt and sitemap.xml for site structure signals."""
    url = _get_website_url(initiative)
    if not url:
        return None

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    lines: list[str] = [f"SITE STRUCTURE: {parsed.netloc}"]

    try:
        robots_text = await _fetch_url(f"{base}/robots.txt")
        if robots_text and "user-agent" in robots_text.lower():
            disallowed = re.findall(r"Disallow:\s*(\S+)", robots_text, re.IGNORECASE)
            sitemaps = re.findall(r"Sitemap:\s*(\S+)", robots_text, re.IGNORECASE)
            if disallowed:
                lines.append(f"  Disallowed paths: {len(disallowed)}")
                for p in disallowed[:10]:
                    lines.append(f"    {p}")
            if sitemaps:
                lines.append(f"  Sitemap URLs declared: {len(sitemaps)}")
    except Exception:
        pass

    sitemap_urls = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml"]
    page_types: dict[str, int] = {}
    all_urls: list[str] = []

    for sitemap_url in sitemap_urls:
        try:
            sitemap_text = await _fetch_url(sitemap_url)
            if not sitemap_text or "<urlset" not in sitemap_text.lower() and "<sitemapindex" not in sitemap_text.lower():
                continue
            all_urls = re.findall(r"<loc>([^<]+)</loc>", sitemap_text)
            for found_url in all_urls[:500]:
                path = urlparse(found_url).path.strip("/")
                prefix = path.split("/")[0] if path else "root"
                page_types[prefix] = page_types.get(prefix, 0) + 1
            break
        except Exception:
            continue

    if all_urls:
        lines.append(f"  Total pages in sitemap: {len(all_urls)}")
        if page_types:
            sorted_types = sorted(page_types.items(), key=lambda x: x[1], reverse=True)
            lines.append("  Site sections:")
            for prefix, count in sorted_types[:10]:
                lines.append(f"    /{prefix}: {count} pages")

    # Identify career/job pages from already-parsed URLs
    for found_url in all_urls:
        path_lower = found_url.lower()
        if any(kw in path_lower for kw in ("career", "job", "stellen", "hiring", "join")):
            lines.append(f"  Career page found: {found_url}")
            break

    if len(lines) <= 1:
        return None
    text = "\n".join(lines)
    return _make_enrichment(initiative, "sitemap", f"{base}/sitemap.xml", text)
