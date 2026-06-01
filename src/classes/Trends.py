"""
Trend discovery for seeding video generation.

Pulls trending / high-engagement titles from Reddit (no API key needed) and
YouTube (requires a YouTube Data API key) so the user can generate a video
around a topic that is already proven to attract attention.

The available niches are NOT hardcoded — `TREND_CATEGORIES` provides suggested
presets, and the caller may pass custom subreddits / keywords instead.
"""

from datetime import datetime, timedelta, timezone
from urllib import parse

import re
import requests

from config import get_youtube_data_api_key, get_trends_region

# Reddit recommends a descriptive UA in the form platform:appid:version.
USER_AGENT = "python:moneyprinterv2:1.0 (trend discovery)"

# Suggested niches. Each maps to a few good subreddits (Reddit) and a search
# phrase (YouTube). These are starting points — the GUI also allows a fully
# custom selection.
TREND_CATEGORIES: dict[str, dict] = {
    "What If / Hypotheticals": {
        "subreddits": ["whatif", "AskReddit", "Showerthoughts", "hypotheticalsituation"],
        "keywords": "what if hypothetical scenario",
    },
    "Science & Space": {
        "subreddits": ["space", "askscience", "science", "EverythingScience"],
        "keywords": "space science facts explained",
    },
    "History & Mysteries": {
        "subreddits": ["history", "AskHistorians", "UnresolvedMysteries", "todayilearned"],
        "keywords": "history mystery documentary",
    },
    "Tech & AI": {
        "subreddits": ["technology", "artificial", "Futurology", "gadgets"],
        "keywords": "AI technology future",
    },
    "Money & Finance": {
        "subreddits": ["personalfinance", "investing", "Entrepreneur", "financialindependence"],
        "keywords": "money finance tips",
    },
    "Health & Fitness": {
        "subreddits": ["Fitness", "nutrition", "loseit", "HealthyFood"],
        "keywords": "health fitness tips",
    },
    "Psychology & Self-improvement": {
        "subreddits": ["psychology", "getdisciplined", "DecidingToBeBetter", "selfimprovement"],
        "keywords": "psychology self improvement",
    },
    "Fun Facts / TIL": {
        "subreddits": ["todayilearned", "Damnthatsinteresting", "interestingasfuck"],
        "keywords": "amazing facts you didn't know",
    },
    "Stories (AskReddit)": {
        "subreddits": ["AskReddit", "tifu", "AmItheAsshole", "stories"],
        "keywords": "reddit stories narrated",
    },
    "Sports": {
        "subreddits": ["sports", "soccer", "nba"],
        "keywords": "sports facts highlights",
    },
    "Gaming": {
        "subreddits": ["gaming", "Games", "pcgaming"],
        "keywords": "gaming facts and secrets",
    },
    "Movies & Pop culture": {
        "subreddits": ["movies", "MovieDetails", "television"],
        "keywords": "movie facts and details",
    },
}


def list_trend_categories() -> list[str]:
    """Returns the suggested category names."""
    return list(TREND_CATEGORIES.keys())


# Map common language names to ISO 639-1 codes for YouTube relevanceLanguage.
_LANGUAGE_CODES = {
    "arabic": "ar", "english": "en", "french": "fr", "spanish": "es",
    "german": "de", "italian": "it", "portuguese": "pt", "turkish": "tr",
    "russian": "ru", "hindi": "hi", "urdu": "ur", "indonesian": "id",
    "japanese": "ja", "korean": "ko", "chinese": "zh",
}


def language_to_code(language: str) -> str:
    """
    Maps a language name to an ISO 639-1 code (e.g. 'Arabic' -> 'ar').

    Args:
        language (str): language name

    Returns:
        code (str): two-letter code, or "" if unknown
    """
    name = str(language or "").strip().lower()
    if not name:
        return ""
    if len(name) == 2:
        return name
    return _LANGUAGE_CODES.get(name, "")


def _clean_title(title: str) -> str:
    """
    Normalizes a source title into a clean seed phrase.

    Args:
        title (str): raw title

    Returns:
        cleaned (str): cleaned title
    """
    text = str(title or "").strip()
    # Drop common Reddit prefixes/tags.
    text = re.sub(r"^\s*(TIL that|TIL|LPT:|PSA:)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[[^\]]*\]", "", text)  # [Serious], [OC], etc.
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _hours_since(epoch_seconds: float) -> float:
    """Returns the number of hours since the given UTC epoch (min 1)."""
    now = datetime.now(timezone.utc).timestamp()
    return max(1.0, (now - float(epoch_seconds or 0)) / 3600.0)


def fetch_reddit_trends(
    subreddits: list[str], limit: int = 15, time_filter: str = "week"
) -> tuple[list[dict], str]:
    """
    Fetches top posts from the given subreddits via Reddit's public JSON.

    Args:
        subreddits (list[str]): subreddit names (without r/)
        limit (int): posts to request per subreddit
        time_filter (str): hour|day|week|month|year|all

    Returns:
        (ideas, note) (tuple[list[dict], str]): candidates and an optional note
    """
    ideas: list[dict] = []
    last_status = 0
    last_error = ""
    for subreddit in subreddits:
        subreddit = str(subreddit or "").strip().lstrip("r/").strip("/")
        if not subreddit:
            continue
        url = (
            f"https://www.reddit.com/r/{parse.quote(subreddit)}/top.json"
            f"?limit={int(limit)}&t={parse.quote(time_filter)}&raw_json=1"
        )
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
            last_status = response.status_code
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            last_error = str(exc)
            continue

        for child in payload.get("data", {}).get("children", []):
            data = child.get("data", {})
            if data.get("stickied") or data.get("over_18"):
                continue
            title = _clean_title(data.get("title", ""))
            if len(title) < 8:
                continue
            score = int(data.get("score", 0) or 0)
            comments = int(data.get("num_comments", 0) or 0)
            velocity = (score + comments * 2) / _hours_since(data.get("created_utc", 0))
            ideas.append(
                {
                    "title": title,
                    "source": f"reddit · r/{subreddit}",
                    "metric_label": f"{score:,} upvotes · {comments:,} comments",
                    "velocity": velocity,
                    "url": "https://www.reddit.com" + str(data.get("permalink", "")),
                }
            )
    ideas.sort(key=lambda idea: idea.get("velocity", 0), reverse=True)

    note = ""
    if not ideas:
        if last_status == 403:
            note = (
                "Reddit returned 403 (blocked). This usually works from a normal home/office "
                "network; cloud/VPN IPs are often blocked. If it persists, try again later."
            )
        elif last_status == 429:
            note = "Reddit rate-limited the request (429). Wait a minute and try again."
        elif last_error:
            note = f"Reddit fetch failed: {last_error}"
    return ideas, note


def fetch_youtube_trends(
    keywords: str,
    region: str = "",
    limit: int = 15,
    days: int = 14,
    language: str | None = None,
) -> tuple[list[dict], str]:
    """
    Fetches recent popular videos for the given keywords via the YouTube Data API.

    Args:
        keywords (str): search phrase
        region (str): ISO region code (e.g. US, EG)
        limit (int): max results
        days (int): only videos published within this many days
        language (str | None): relevance language hint

    Returns:
        (ideas, note) (tuple[list[dict], str]): candidates and an optional note
    """
    api_key = get_youtube_data_api_key()
    if not api_key:
        return [], "YouTube source skipped: set youtube_data_api_key (or YOUTUBE_DATA_API_KEY) to enable it."
    if not str(keywords or "").strip():
        return [], ""

    published_after = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "part": "snippet",
        "type": "video",
        "order": "viewCount",
        "q": keywords,
        "maxResults": str(min(int(limit), 50)),
        "publishedAfter": published_after,
        "key": api_key,
    }
    if region:
        params["regionCode"] = region
    if language:
        params["relevanceLanguage"] = language

    url = "https://www.googleapis.com/youtube/v3/search?" + parse.urlencode(params)
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return [], f"YouTube fetch failed: {exc}"

    ideas: list[dict] = []
    for index, item in enumerate(payload.get("items", [])):
        video_id = item.get("id", {}).get("videoId")
        snippet = item.get("snippet", {})
        title = _clean_title(snippet.get("title", ""))
        if not video_id or len(title) < 8:
            continue
        ideas.append(
            {
                "title": title,
                "source": "youtube · trending search",
                "metric_label": "recent popular",
                # Preserve API rank (already ordered by viewCount).
                "velocity": float(limit - index),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    return ideas, ""


def fetch_youtube_region_trending(
    region: str = "US", limit: int = 20
) -> tuple[list[dict], str]:
    """
    Fetches the videos actually trending in a region via the mostPopular chart.

    Args:
        region (str): ISO region code (e.g. EG)
        limit (int): max results

    Returns:
        (ideas, note) (tuple[list[dict], str])
    """
    api_key = get_youtube_data_api_key()
    if not api_key:
        return [], "YouTube source skipped: set youtube_data_api_key (or YOUTUBE_DATA_API_KEY) to enable it."

    params = {
        "part": "snippet,statistics",
        "chart": "mostPopular",
        "regionCode": region or "US",
        "maxResults": str(min(int(limit), 50)),
        "key": api_key,
    }
    url = "https://www.googleapis.com/youtube/v3/videos?" + parse.urlencode(params)
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return [], f"YouTube region-trending fetch failed: {exc}"

    ideas: list[dict] = []
    for item in payload.get("items", []):
        video_id = item.get("id")
        snippet = item.get("snippet", {})
        title = _clean_title(snippet.get("title", ""))
        if not video_id or len(title) < 6:
            continue
        views = int(item.get("statistics", {}).get("viewCount", 0) or 0)
        ideas.append(
            {
                "title": title,
                "source": f"youtube · trending in {region or 'US'}",
                "metric_label": f"{views:,} views",
                "velocity": float(views),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    ideas.sort(key=lambda idea: idea.get("velocity", 0), reverse=True)
    return ideas, ""


def get_trending_ideas(
    category: str | None = None,
    custom_subreddits: list[str] | None = None,
    keywords: str | None = None,
    sources: tuple[str, ...] = ("reddit",),
    region: str | None = None,
    limit: int = 25,
    language: str | None = None,
    youtube_mode: str = "search",
) -> tuple[list[dict], list[str]]:
    """
    Returns ranked trend candidates from the selected sources.

    Args:
        category (str | None): a key from TREND_CATEGORIES (ignored if custom
            subreddits/keywords are provided)
        custom_subreddits (list[str] | None): override subreddits
        keywords (str | None): override YouTube search phrase
        sources (tuple[str, ...]): any of "reddit", "youtube"
        region (str | None): YouTube region code (defaults to config)
        limit (int): max total ideas to return
        language (str | None): YouTube relevance language code (e.g. "ar")
        youtube_mode (str): "search" (niche keywords) or "region" (what's
            actually trending in the region via the mostPopular chart)

    Returns:
        (ideas, notes) (tuple[list[dict], list[str]]): candidates and notes
    """
    preset = TREND_CATEGORIES.get(category or "", {})
    subreddits = custom_subreddits if custom_subreddits else preset.get("subreddits", [])
    youtube_keywords = keywords if keywords else preset.get("keywords", category or "")
    region = region if region is not None else get_trends_region()

    reddit_ideas: list[dict] = []
    youtube_ideas: list[dict] = []
    notes: list[str] = []

    if "reddit" in sources and subreddits:
        reddit_ideas, reddit_note = fetch_reddit_trends(subreddits)
        if reddit_note:
            notes.append(reddit_note)
    if "youtube" in sources:
        if youtube_mode == "region":
            youtube_ideas, note = fetch_youtube_region_trending(region=region, limit=limit)
        elif youtube_keywords:
            youtube_ideas, note = fetch_youtube_trends(
                youtube_keywords, region=region, language=language
            )
        else:
            note = ""
        if note:
            notes.append(note)

    # Interleave so both sources are represented, then cap.
    merged: list[dict] = []
    seen: set[str] = set()
    for reddit_item, youtube_item in _zip_longest(reddit_ideas, youtube_ideas):
        for item in (reddit_item, youtube_item):
            if not item:
                continue
            key = item["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

    return merged[:limit], notes


def _zip_longest(first: list, second: list):
    """Minimal itertools.zip_longest replacement yielding (a, b) pairs."""
    length = max(len(first), len(second))
    for index in range(length):
        yield (
            first[index] if index < len(first) else None,
            second[index] if index < len(second) else None,
        )
