"""Data access helpers for SofaScore and local replay fixtures."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from curl_cffi import requests

from .utils import (
    TEAM_ROW_HEADER,
    apply_result_to_split_tables,
    build_table_from_team_stats,
    stable_seed_from_text,
    team_stats_from_table,
)


LOGGER = logging.getLogger(__name__)

# curl_cffi accepts many impersonation profiles; keep this a plain string so new
# profiles can be used without touching this module.
BrowserImpersonation = str

# api.sofascore.com answers 403 "challenge" to every impersonation profile, while
# www.sofascore.com/api/v1 serves the same JSON to Safari profiles. The primary pair
# is the one verified to work; the others are tried only after a 403.
DEFAULT_BASE_URL = "https://www.sofascore.com/api/v1"
FALLBACK_BASE_URLS: tuple[str, ...] = ("https://api.sofascore.com/api/v1",)
DEFAULT_IMPERSONATE: BrowserImpersonation = "safari17_0"
FALLBACK_IMPERSONATIONS: tuple[BrowserImpersonation, ...] = (
    "safari18_0",
    "safari15_5",
    "chrome124",
)
ENV_BASE_URL = "SOFASCORE_BASE_URL"
ENV_IMPERSONATE = "SOFASCORE_IMPERSONATE"

# Host of the pre-metadata cache layout. Only used to detect (and log) legacy files.
LEGACY_BASE_URL = "https://api.sofascore.com/api/v1"

# No User-Agent here on purpose: curl_cffi sends the UA that belongs to the selected
# impersonation profile, which keeps UA and TLS fingerprint consistent.
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

CACHE_SCHEMA = 2
CACHE_FILE_PREFIX = "v2-"

KIND_SEASONS = "seasons"
KIND_SEASON_DATA = "season-data"
KIND_OTHER = "other"

SOURCE_NETWORK = "network"
SOURCE_CACHE = "cache"
SOURCE_MIXED = "mixed"
SOURCE_SNAPSHOT = "snapshot"

STATUS_IN_PROGRESS = "in_progress"
STATUS_NOT_STARTED = "not_started"
STATUS_FINISHED = "finished"
STATUS_UNKNOWN = "unknown"

# Auto-selection looks at most this many of the newest listed seasons.
MAX_SEASONS_INSPECTED = 3
# A finished season is "settled" (its data cannot change any more) this long after
# its last match; only then do its cache entries get the long TTL.
SETTLE_SECONDS = 2 * 24 * 3600.0
# Live/postponed matches older than this no longer keep a season "in progress".
PENDING_MAX_AGE_SECONDS = 14 * 24 * 3600.0
# Safety stop for pagination loops (SofaScore pages hold ~30 events).
MAX_EVENT_PAGES = 100
# Cache entries stamped further in the future than this are distrusted.
CLOCK_SKEW_SECONDS = 300.0

_SEASON_PATH_RE = re.compile(r"^/unique-tournament/(\d+)/season/(\d+)(?:/|$)")


class SofaScoreError(RuntimeError):
    """Base class for data-access failures."""


class SofaScoreBlockedError(SofaScoreError):
    """Every host/impersonation combination was answered with HTTP 403."""


class SofaScoreNotFoundError(SofaScoreError):
    """The requested resource does not exist (HTTP 404)."""


class SeasonSelectionError(SofaScoreError):
    """No sensible season could be selected automatically."""


class SeasonNotFoundError(SeasonSelectionError, ValueError):
    """An explicitly requested season id/year is not listed for the tournament.

    Also a ``ValueError`` so callers that predate this class keep working.
    """


class _ChallengeError(Exception):
    """Internal: one candidate answered HTTP 403."""


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """Time-to-live rules for the disk cache, in seconds."""

    seasons_ttl: float = 24 * 3600.0
    current_season_ttl: float = 30 * 60.0
    finished_season_ttl: float = 30 * 24 * 3600.0

    def ttl_for(self, kind: str, finished: bool) -> float:
        if kind == KIND_SEASONS:
            return self.seasons_ttl
        if kind == KIND_SEASON_DATA and finished:
            return self.finished_season_ttl
        return self.current_season_ttl


@dataclass(frozen=True, slots=True)
class FetchRecord:
    """Where one piece of data came from and when it was originally fetched."""

    path: str
    url: str
    kind: str
    source: str
    fetched_at: float


@dataclass(slots=True)
class _MemoryEntry:
    payload: Any
    record: FetchRecord


@dataclass(frozen=True, slots=True)
class SeasonState:
    """Coarse lifecycle state of a season, derived from standings and fixtures."""

    status: str
    matches_played: int
    has_fixtures: bool
    # True once the season ended long enough ago that its data can no longer change.
    settled: bool = False
    # Matches that are neither played nor merely scheduled (live, postponed, ...).
    # They keep a season "in progress" and are never simulated nor given a score.
    pending: int = 0


# Event status types that are either a plain scheduled match or a definitive outcome.
_SETTLED_OR_SCHEDULED_TYPES = frozenset(
    {"notstarted", "finished", "canceled", "cancelled"}
)


def _is_pending_event(event: dict[str, Any], now: float) -> bool:
    """True for a live, postponed or interrupted match that still may produce a result.

    Events whose start time is older than ``PENDING_MAX_AGE_SECONDS`` are ignored: a
    match postponed months ago and never replayed must not keep a season "in progress".
    """
    status_type = (event.get("status") or {}).get("type")
    if not status_type or status_type in _SETTLED_OR_SCHEDULED_TYPES:
        return False
    start = event.get("startTimestamp")
    too_old = isinstance(start, (int, float)) and start < now - PENDING_MAX_AGE_SECONDS
    return not too_old


@dataclass(slots=True)
class SeasonSelection:
    """Outcome of resolving which season to simulate."""

    season: dict[str, Any]
    state: SeasonState
    warnings: list[str] = field(default_factory=list)

    @property
    def season_id(self) -> int | None:
        return self.season.get("id")

    @property
    def status(self) -> str:
        return self.state.status


def _challenge_reason(response: Any) -> str:
    try:
        return str(response.json().get("error", {}).get("reason") or "")
    except Exception:
        return ""


def _select_standing(standings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the aggregate standings block.

    Some competitions expose several groups (e.g. MLS conferences plus an overall
    table). Prefer the largest block that is not a conference table.
    """
    if not standings:
        return None
    return max(
        standings,
        key=lambda standing: (
            len(standing.get("rows", [])),
            "conference" not in str(standing.get("name", "")).lower(),
        ),
    )


def format_age(seconds: float) -> str:
    """Format an age compactly, e.g. '12m 03s' or '3d 4h'."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def iso_utc(timestamp: float) -> str:
    """Render a Unix timestamp as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(value: Any) -> float | None:
    """Parse an ISO-8601 timestamp (Python 3.10 safe, accepts a trailing 'Z')."""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _canonical_path(url: str) -> str:
    """Reduce a URL (or bare path) to its host-independent API path."""
    path = urlsplit(url).path if "://" in url else url
    marker = "/api/v1"
    index = path.find(marker)
    if index != -1:
        path = path[index + len(marker) :]
    return path if path.startswith("/") else f"/{path}"


def _classify_path(path: str) -> str:
    if path.endswith("/seasons"):
        return KIND_SEASONS
    if _SEASON_PATH_RE.match(path):
        return KIND_SEASON_DATA
    return KIND_OTHER


def _season_key(path: str) -> tuple[int, int] | None:
    match = _SEASON_PATH_RE.match(path)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _season_start_year(season: dict[str, Any]) -> int:
    match = re.match(r"\s*(\d{2,4})", str(season.get("year", "")))
    if match is None:
        return 0
    year = int(match.group(1))
    if year < 100:
        year += 2000 if year < 70 else 1900
    return year


def _describe_season(season: dict[str, Any]) -> str:
    name = season.get("name") or season.get("year") or "unknown season"
    return f"{name} (id {season.get('id')})"


@dataclass(slots=True)
class ReplaySnapshot:
    """A replayable dataset for simulation or backtesting."""

    tournament_id: int
    season_id: int
    league_name: str
    snapshot_label: str
    base_table: list[list]
    home_table: list[list]
    away_table: list[list]
    fixtures: list[dict[str, Any]]
    team_colors: dict[str, dict[str, str | None]]
    metadata: dict[str, Any]


class SofaScoreClient:
    """HTTP client for fetching football data from SofaScore.

    Network access
        Requests go to ``base_url`` with the ``impersonate`` TLS profile. Both are
        configurable (constructor, ``SOFASCORE_BASE_URL`` / ``SOFASCORE_IMPERSONATE``
        or the CLI flags). When a request is answered with HTTP 403 the remaining
        host/impersonation combinations are tried before failing with
        :class:`SofaScoreBlockedError`. The working combination is remembered.

    Disk cache
        Entries live in ``cache_dir`` as ``v2-<sha256(path)>.json`` and carry
        ``fetched_at`` and the URL. They are keyed by the host-independent API path
        (which contains the season id) and expire by kind of data, see
        :class:`CachePolicy`. Files without metadata (the pre-v2 layout) are never
        read and never deleted.
    """

    BASE_URL = DEFAULT_BASE_URL

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        use_disk_cache: bool = True,
        force_refresh: bool = False,
        timeout: int = 30,
        retries: int = 3,
        retry_delay: float = 1.0,
        impersonate: BrowserImpersonation | None = None,
        base_url: str | None = None,
        fallback: bool = True,
        cache_policy: CachePolicy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.impersonate: BrowserImpersonation = (
            impersonate or os.environ.get(ENV_IMPERSONATE) or DEFAULT_IMPERSONATE
        )
        self.base_url = (
            base_url or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL
        ).rstrip("/")
        self.fallback = fallback
        self.cache_policy = cache_policy or CachePolicy()
        self.cache_dir = cache_dir or Path(".cache") / "sofascore"
        self.use_disk_cache = use_disk_cache
        self.force_refresh = force_refresh
        self.session = requests.Session(headers=DEFAULT_HEADERS)
        self._clock = clock
        self._memory: dict[str, _MemoryEntry] = {}
        self._refreshed: set[str] = set()
        self._states: dict[tuple[int, int], SeasonState] = {}
        self._active: tuple[str, str] | None = None
        self.records: dict[str, FetchRecord] = {}

    # ------------------------------------------------------------------ cache

    def _cache_path(self, url: str) -> Path:
        digest = sha256(_canonical_path(url).encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{CACHE_FILE_PREFIX}{digest}.json"

    def _legacy_cache_path(self, path: str) -> Path:
        legacy_url = f"{LEGACY_BASE_URL}{path}"
        return self.cache_dir / f"{stable_seed_from_text(legacy_url):010d}.json"

    def _read_disk(self, path: str, kind: str) -> _MemoryEntry | None:
        """Return a fresh cache entry, or None when missing/stale/legacy."""
        cache_path = self._cache_path(path)
        if not cache_path.exists():
            legacy = self._legacy_cache_path(path)
            if legacy.exists():
                LOGGER.info(
                    "Ignoring legacy cache file %s for %s: it has no metadata, "
                    "so it is treated as stale",
                    legacy.name,
                    path,
                )
            return None
        try:
            raw = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            LOGGER.warning("Unreadable cache file %s (%s); refetching", cache_path.name, exc)
            return None

        fetched_at = raw.get("fetched_at_ts") if isinstance(raw, dict) else None
        if (
            not isinstance(raw, dict)
            or raw.get("schema") != CACHE_SCHEMA
            or "payload" not in raw
            or raw.get("path") != path
            or isinstance(fetched_at, bool)
            or not isinstance(fetched_at, (int, float))
        ):
            LOGGER.info(
                "Cache file %s for %s has no valid metadata; treated as stale",
                cache_path.name,
                path,
            )
            return None

        age = self._clock() - float(fetched_at)
        ttl = self.cache_policy.ttl_for(kind, bool(raw.get("finished")))
        if age < -CLOCK_SKEW_SECONDS or age >= ttl:
            LOGGER.info(
                "Cache entry for %s expired (age %s, ttl %s); refetching",
                path,
                format_age(age),
                format_age(ttl),
            )
            return None
        record = FetchRecord(
            path=path,
            url=str(raw.get("url") or path),
            kind=kind,
            source=SOURCE_CACHE,
            fetched_at=float(fetched_at),
        )
        return _MemoryEntry(raw["payload"], record)

    def _write_disk(self, entry: _MemoryEntry, finished: bool) -> None:
        record = entry.record
        cache_path = self._cache_path(record.path)
        document = {
            "schema": CACHE_SCHEMA,
            "url": record.url,
            "path": record.path,
            "kind": record.kind,
            "fetched_at": iso_utc(record.fetched_at),
            "fetched_at_ts": record.fetched_at,
            "finished": finished,
            "payload": entry.payload,
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = cache_path.with_name(cache_path.name + ".tmp")
            tmp_path.write_text(json.dumps(document), encoding="utf-8")
            os.replace(tmp_path, cache_path)
        except OSError as exc:
            LOGGER.warning("Could not write cache file %s: %s", cache_path, exc)

    def _remember(self, entry: _MemoryEntry, *, refreshed: bool) -> None:
        self._memory[entry.record.path] = entry
        self.records[entry.record.path] = entry.record
        if refreshed:
            self._refreshed.add(entry.record.path)

    # ---------------------------------------------------------------- network

    def _candidates(self) -> list[tuple[str, str]]:
        primary = (self.base_url, self.impersonate)
        if not self.fallback:
            return [primary]
        hosts = [self.base_url] + [
            host for host in (DEFAULT_BASE_URL, *FALLBACK_BASE_URLS) if host != self.base_url
        ]
        profiles = [self.impersonate] + [
            profile
            for profile in (DEFAULT_IMPERSONATE, *FALLBACK_IMPERSONATIONS)
            if profile != self.impersonate
        ]
        ordered = list(dict.fromkeys((host, profile) for host in hosts for profile in profiles))
        if self._active in ordered:
            ordered.remove(self._active)
            ordered.insert(0, self._active)
        return ordered

    def _get_with_retries(self, url: str, impersonate: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    impersonate=cast(Any, impersonate),
                    timeout=self.timeout,
                )
                status = response.status_code
                if status == 403:
                    raise _ChallengeError(_challenge_reason(response))
                if status == 404:
                    raise SofaScoreNotFoundError(f"SofaScore returned HTTP 404 for {url}")
                if status >= 400:
                    raise SofaScoreError(f"HTTP {status}")
                return response.json()
            except (_ChallengeError, SofaScoreNotFoundError):
                raise
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "Failed to fetch %s on attempt %s/%s: %s",
                    url,
                    attempt,
                    self.retries,
                    exc,
                )
                if attempt < self.retries:
                    time.sleep(self.retry_delay * attempt)
        raise SofaScoreError(f"Could not fetch SofaScore URL: {url}") from last_error

    def _download(self, path: str) -> tuple[Any, str]:
        """GET ``path``, falling back to other host/impersonation pairs on 403."""
        candidates = self._candidates()
        tried: list[str] = []
        reason = "challenge"
        for index, (base, profile) in enumerate(candidates):
            url = f"{base}{path}"
            try:
                payload = self._get_with_retries(url, profile)
            except _ChallengeError as exc:
                reason = str(exc) or reason
                tried.append(f"{base} [{profile}]")
                LOGGER.warning(
                    "HTTP 403 (%s) from %s with impersonation %s%s",
                    reason,
                    base,
                    profile,
                    "; trying the next alternative" if index + 1 < len(candidates) else "",
                )
                continue
            if tried:
                LOGGER.warning(
                    "Fallback succeeded with %s [%s] after %s blocked attempt(s)",
                    base,
                    profile,
                    len(tried),
                )
            self._active = (base, profile)
            return payload, url
        raise SofaScoreBlockedError(
            f'SofaScore answered HTTP 403 ("{reason}" challenge) for every host/'
            f"impersonation combination tried ({'; '.join(tried)}). The site is "
            "blocking this client right now. Re-run with --snapshot-file <file> to "
            "simulate from a saved snapshot (tests/fixtures/sample_snapshot.json "
            "shows the format), or choose another combination with --base-url / "
            "--impersonate (env SOFASCORE_BASE_URL / SOFASCORE_IMPERSONATE)."
        )

    def _fetch(
        self, url: str, *, force_refresh: bool = False, probing: bool = False
    ) -> _MemoryEntry:
        path = _canonical_path(url)
        force = force_refresh or self.force_refresh
        cached = self._memory.get(path)
        if cached is not None and (not force or path in self._refreshed):
            LOGGER.debug("Memory hit for %s", path)
            return cached

        kind = _classify_path(path)
        if self.use_disk_cache and not force:
            entry = self._read_disk(path, kind)
            if entry is not None:
                self._remember(entry, refreshed=False)
                LOGGER.info(
                    "Data from cache: %s (fetched %s, age %s)",
                    path,
                    iso_utc(entry.record.fetched_at),
                    format_age(self._clock() - entry.record.fetched_at),
                )
                return entry

        payload, full_url = self._download(path)
        entry = _MemoryEntry(
            payload,
            FetchRecord(
                path=path,
                url=full_url,
                kind=kind,
                source=SOURCE_NETWORK,
                fetched_at=self._clock(),
            ),
        )
        # Remember first: deciding whether the season is settled may need this very
        # resource (standings), which must then come from memory, not a second GET.
        self._remember(entry, refreshed=force)
        LOGGER.info("Data from network: %s", path)
        if self.use_disk_cache:
            finished = (
                not probing and kind == KIND_SEASON_DATA and self._is_settled(path)
            )
            self._write_disk(entry, finished)
        return entry

    def fetch_json(self, url: str, *, force_refresh: bool = False) -> Any:
        """Fetch JSON for a full URL or API path, using memory/disk cache and retries."""
        return self._fetch(url, force_refresh=force_refresh).payload

    def data_summary(
        self, tournament_id: int | None = None, season_id: int | None = None
    ) -> dict[str, Any]:
        """Describe where the simulation inputs of this run came from.

        With ``tournament_id`` and ``season_id`` only that season's resources count:
        probes of seasons that were inspected but not selected must not influence the
        reported source or age. The season list is left out (it is selection input,
        not simulation input) unless nothing else was fetched.
        """
        scoped = [
            r
            for r in self.records.values()
            if r.kind != KIND_SEASONS
            and (
                tournament_id is None
                or season_id is None
                or _season_key(r.path) == (tournament_id, season_id)
            )
        ]
        records = scoped or [
            r
            for r in self.records.values()
            if tournament_id is None or season_id is None or r.kind == KIND_SEASONS
        ]
        if not records:
            return {}
        sources = {record.source for record in records}
        source = SOURCE_MIXED if len(sources) > 1 else next(iter(sources))
        oldest = min(record.fetched_at for record in records)
        newest = max(record.fetched_at for record in records)
        summary: dict[str, Any] = {
            "data_source": source,
            "fetched_at": iso_utc(oldest),
            "newest_fetched_at": iso_utc(newest),
            "data_age_seconds": round(max(0.0, self._clock() - oldest), 1),
            "network_requests": sum(r.source == SOURCE_NETWORK for r in records),
            "cache_hits": sum(r.source == SOURCE_CACHE for r in records),
        }
        if SOURCE_NETWORK in sources and self._active is not None:
            summary["api_base_url"], summary["impersonate"] = self._active
        return summary

    # ---------------------------------------------------------------- seasons

    def get_seasons(self, tournament_id: int) -> list[dict[str, Any]]:
        """Return all seasons for a tournament."""
        data = self.fetch_json(f"/unique-tournament/{tournament_id}/seasons")
        return data.get("seasons", [])

    def season_state(self, tournament_id: int, season_id: int) -> SeasonState:
        """Classify a season as not started / in progress / finished.

        The state is itself cached (short TTL, long once a season has settled).
        """
        key = (tournament_id, season_id)
        known = self._states.get(key)
        if known is not None:
            return known

        path = f"/unique-tournament/{tournament_id}/season/{season_id}/_state"
        if self.use_disk_cache and not self.force_refresh:
            entry = self._read_disk(path, KIND_SEASON_DATA)
            if entry is not None:
                try:
                    state = SeasonState(**entry.payload)
                except TypeError:
                    LOGGER.info("Cached season state for %s is malformed; recomputing", path)
                else:
                    self._states[key] = state
                    return state

        state, probes = self._compute_season_state(tournament_id, season_id)
        self._states[key] = state
        if self.use_disk_cache:
            self._write_disk(
                _MemoryEntry(
                    {
                        "status": state.status,
                        "matches_played": state.matches_played,
                        "has_fixtures": state.has_fixtures,
                        "settled": state.settled,
                        "pending": state.pending,
                    },
                    FetchRecord(path, "derived", KIND_SEASON_DATA, SOURCE_NETWORK, self._clock()),
                ),
                finished=state.settled,
            )
            if state.settled:
                # The probes were written before the season was known to be settled.
                for probe in probes:
                    self._write_disk(probe, finished=True)
        return state

    def _is_settled(self, path: str) -> bool:
        key = _season_key(path)
        return key is not None and self.season_state(*key).settled

    def _compute_season_state(
        self, tournament_id: int, season_id: int
    ) -> tuple[SeasonState, list[_MemoryEntry]]:
        base = f"/unique-tournament/{tournament_id}/season/{season_id}"
        probes: list[_MemoryEntry] = []

        def probe(suffix: str) -> Any | None:
            try:
                entry = self._fetch(f"{base}{suffix}", probing=True)
            except SofaScoreNotFoundError:
                return None
            probes.append(entry)
            return entry.payload

        upcoming_events: list[dict[str, Any]] = (
            probe("/events/next/0") or {}
        ).get("events", [])
        remaining = sum(
            1
            for event in upcoming_events
            if (event.get("status") or {}).get("type") == "notstarted"
        )
        standings = probe("/standings/total") or {}
        selected = _select_standing(standings.get("standings", []))
        rows = selected.get("rows", []) if selected else []
        matches_played = sum(int(row.get("matches", 0) or 0) for row in rows) // 2

        # The latest played/pending matches are needed to (a) tell a finished season
        # from one whose last matches are live or postponed, (b) date the season's end,
        # and (c) not mistake an in-progress season with missing standings for a
        # pre-season one (which would otherwise be simulated from an empty table).
        recent_events: list[dict[str, Any]] = []
        if not remaining or not matches_played:
            recent_events = (probe("/events/last/0") or {}).get("events", [])
        if not matches_played:
            matches_played = sum(
                1
                for event in recent_events
                if (event.get("status") or {}).get("type") == "finished"
            )
        now = self._clock()
        pending = sum(
            _is_pending_event(event, now) for event in (*upcoming_events, *recent_events)
        )

        settled = False
        if remaining or pending:
            status = STATUS_IN_PROGRESS if matches_played else STATUS_NOT_STARTED
        elif matches_played:
            status = STATUS_FINISHED
            stamps = [
                event["startTimestamp"]
                for event in recent_events
                if isinstance(event.get("startTimestamp"), (int, float))
            ]
            settled = bool(stamps) and max(stamps) < self._clock() - SETTLE_SECONDS
        else:
            status = STATUS_NOT_STARTED

        return (
            SeasonState(
                status=status,
                matches_played=matches_played,
                has_fixtures=remaining > 0,
                settled=settled,
                pending=pending,
            ),
            probes,
        )

    def select_season(
        self,
        tournament_id: int,
        *,
        season_id: int | None = None,
        season_year: str | None = None,
    ) -> SeasonSelection:
        """Pick the season to simulate and explain the choice.

        Explicit ``season_id`` / ``season_year`` are honoured (with a warning when the
        season is finished or has not started). Automatic selection inspects the
        newest seasons, newest first:

        * the first season that is *in progress* (played matches and fixtures left) wins;
        * a *not started* season is only used when no in-progress season exists, with
          a warning that the full season is simulated from an empty table, and only if
          it has fixtures;
        * a *finished* season stops the search; when nothing else qualifies the
          selection fails with :class:`SeasonSelectionError` instead of silently
          simulating an old season.
        """
        seasons = self.get_seasons(tournament_id)

        if season_id is not None or season_year:
            season = self._find_season(
                tournament_id, seasons, season_id=season_id, season_year=season_year
            )
            state = self.season_state(tournament_id, cast(int, season["id"]))
            warnings: list[str] = []
            label = _describe_season(season)
            if state.status == STATUS_FINISHED:
                warnings.append(
                    f"Season {label} is finished (0 remaining fixtures); there is nothing left to simulate."
                )
            elif state.status == STATUS_NOT_STARTED:
                warnings.append(
                    f"Season {label} has not started yet; the full season is simulated from an empty table."
                )
            return SeasonSelection(season, state, warnings)

        ordered = sorted(
            (season for season in seasons if season.get("id") is not None),
            key=lambda season: (_season_start_year(season), season["id"]),
            reverse=True,
        )
        if not ordered:
            raise SeasonSelectionError(
                f"SofaScore lists no seasons for tournament {tournament_id}."
            )

        upcoming: tuple[dict[str, Any], SeasonState] | None = None
        skipped: list[tuple[str, str]] = []
        newest_state: SeasonState | None = None
        for season in ordered[:MAX_SEASONS_INSPECTED]:
            state = self.season_state(tournament_id, season["id"])
            newest_state = newest_state or state
            label = _describe_season(season)
            if state.status == STATUS_IN_PROGRESS:
                warnings = [
                    f"Newer season {name} is not usable ({why}); using {label}."
                    for name, why in skipped
                ]
                return SeasonSelection(season, state, warnings)
            if state.status == STATUS_FINISHED:
                skipped.append((label, "finished"))
                break
            if upcoming is None and state.has_fixtures:
                upcoming = (season, state)
            skipped.append((label, "not started"))

        if upcoming is not None:
            season, state = upcoming
            return SeasonSelection(
                season,
                state,
                [
                    (
                        f"Season {_describe_season(season)} has not started yet and no season "
                        "is in progress; simulating the full season from an empty table. Pass "
                        "--season-year/--season-id to choose another season."
                    )
                ],
            )

        newest = ordered[0]
        reason = (
            "is finished (0 remaining fixtures)"
            if newest_state and newest_state.status == STATUS_FINISHED
            else "has not started and has no fixtures scheduled yet"
        )
        raise SeasonSelectionError(
            f"The newest SofaScore season {_describe_season(newest)} {reason}, and no "
            "other recent season is in progress, so there is nothing to simulate. "
            "Pass --season-year (e.g. 25/26) or --season-id to pick a season explicitly."
        )

    @staticmethod
    def _find_season(
        tournament_id: int,
        seasons: list[dict[str, Any]],
        *,
        season_id: int | None,
        season_year: str | None,
    ) -> dict[str, Any]:
        listed = ", ".join(
            str(season.get("year") or season.get("name")) for season in seasons[:8]
        )
        if season_id is not None:
            for season in seasons:
                if season.get("id") == season_id:
                    return season
            raise SeasonNotFoundError(
                f"Season id {season_id} is not listed for tournament {tournament_id}. "
                f"Newest listed seasons: {listed or 'none'}."
            )
        for season in seasons:
            if (
                str(season.get("year")) == season_year
                or str(season.get("name")) == season_year
            ):
                return season
        raise SeasonNotFoundError(
            f"Season '{season_year}' not found for tournament {tournament_id}. "
            f"Newest listed seasons: {listed or 'none'}."
        )

    def get_current_season_id(self, tournament_id: int) -> int | None:
        """Return the id of the season :meth:`select_season` would pick, if any."""
        try:
            return self.select_season(tournament_id).season_id
        except SeasonSelectionError as exc:
            LOGGER.warning("%s", exc)
            return None

    def select_backtest_season(
        self,
        tournament_id: int,
        *,
        season_id: int | None = None,
        season_year: str | None = None,
    ) -> SeasonSelection:
        """Pick the season to replay for a backtest.

        Unlike :meth:`select_season` (which wants something left to *predict*), a
        backtest needs completed matches to replay. An explicit season is honoured
        as is. Otherwise the newest of the three newest seasons that has played
        matches is used: a finished one is ideal, an in-progress one is acceptable
        (only its played part can be replayed).
        """
        seasons = self.get_seasons(tournament_id)
        if season_id is not None or season_year:
            season = self._find_season(
                tournament_id, seasons, season_id=season_id, season_year=season_year
            )
            return SeasonSelection(
                season, self.season_state(tournament_id, cast(int, season["id"]))
            )

        ordered = sorted(
            (season for season in seasons if season.get("id") is not None),
            key=lambda season: (_season_start_year(season), season["id"]),
            reverse=True,
        )
        for season in ordered[:MAX_SEASONS_INSPECTED]:
            state = self.season_state(tournament_id, season["id"])
            if state.matches_played > 0:
                warnings: list[str] = []
                if state.status == STATUS_IN_PROGRESS:
                    warnings.append(
                        f"Season {_describe_season(season)} is still in progress; only its "
                        "played matches can be replayed. Use --season-year for a finished season."
                    )
                return SeasonSelection(season, state, warnings)
        raise SeasonSelectionError(
            f"None of the {min(len(ordered), MAX_SEASONS_INSPECTED)} newest seasons of "
            f"tournament {tournament_id} has played matches to replay. "
            "Pass --season-year (e.g. 25/26) or --season-id."
        )

    def resolve_season_id(
        self,
        tournament_id: int,
        *,
        season_id: int | None = None,
        season_year: str | None = None,
    ) -> int | None:
        """Resolve a season either by id, explicit year string or current season.

        An explicit ``season_id`` is validated against the listed seasons and raises
        :class:`SeasonNotFoundError` when unknown.
        """
        if season_id is not None or season_year:
            seasons = self.get_seasons(tournament_id)
            return self._find_season(
                tournament_id, seasons, season_id=season_id, season_year=season_year
            ).get("id")
        return self.get_current_season_id(tournament_id)

    def _build_table(self, rows: list[dict[str, Any]]) -> list[list]:
        table_rows = [TEAM_ROW_HEADER.copy()]
        for row in rows:
            table_rows.append(
                [
                    row["team"]["name"],
                    row.get("matches", 0),
                    row.get("wins", 0),
                    row.get("draws", 0),
                    row.get("losses", 0),
                    row.get("scoresFor", 0),
                    row.get("scoresAgainst", 0),
                    row.get("points", 0),
                ]
            )
        return table_rows

    def get_standings(
        self, tournament_id: int, season_id: int, scope: str
    ) -> list[list] | None:
        """Get standings for a given scope.

        Returns ``None`` when SofaScore publishes no table (HTTP 404, an empty
        ``standings`` list, or a table without rows), as happens before a season starts.
        """
        try:
            data = self.fetch_json(
                f"/unique-tournament/{tournament_id}/season/{season_id}/standings/{scope}"
            )
        except SofaScoreNotFoundError:
            return None
        selected_standing = _select_standing(data.get("standings", []))
        if selected_standing is None or not selected_standing.get("rows"):
            return None
        return self._build_table(selected_standing.get("rows", []))

    def get_league_table(self, tournament_id: int, season_id: int) -> list[list] | None:
        return self.get_standings(tournament_id, season_id, "total")

    def get_home_league_table(
        self, tournament_id: int, season_id: int
    ) -> list[list] | None:
        return self.get_standings(tournament_id, season_id, "home")

    def get_away_league_table(
        self, tournament_id: int, season_id: int
    ) -> list[list] | None:
        return self.get_standings(tournament_id, season_id, "away")

    def get_team_colors(
        self, tournament_id: int, season_id: int
    ) -> dict[str, dict[str, str | None]]:
        """Extract team colors from standings data (empty when none are published)."""
        try:
            data = self.fetch_json(
                f"/unique-tournament/{tournament_id}/season/{season_id}/standings/total"
            )
        except SofaScoreNotFoundError:
            return {}
        team_colors: dict[str, dict[str, str | None]] = {}
        default_blue = "#374df5"
        for standing in data.get("standings", []):
            for row in standing.get("rows", []):
                team = row.get("team", {})
                name = team.get("name")
                if not name:
                    continue
                team_colors_data = team.get("teamColors", {})
                primary = team_colors_data.get("primary")
                secondary = team_colors_data.get("secondary")
                team_colors[name] = {
                    "primary": primary if primary and primary != default_blue else None,
                    "secondary": secondary
                    if secondary and secondary != default_blue
                    else None,
                }
        return team_colors

    def _event_to_fixture(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": event["id"],
            "h": {"title": event["homeTeam"]["name"]},
            "a": {"title": event["awayTeam"]["name"]},
            "datetime": event.get("startTimestamp"),
            "status": event.get("status", {}).get("type"),
        }

    def get_events_page(
        self,
        tournament_id: int,
        season_id: int,
        direction: str,
        page: int,
    ) -> dict[str, Any]:
        """Return a page of events in a given direction."""
        return self.fetch_json(
            f"/unique-tournament/{tournament_id}/season/{season_id}/events/{direction}/{page}"
        )

    def _collect_events(
        self,
        tournament_id: int,
        season_id: int,
        direction: str,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        page = 0
        while True:
            try:
                data = self.get_events_page(tournament_id, season_id, direction, page)
            except SofaScoreNotFoundError:
                # SofaScore answers 404 for e.g. "next" events of a finished season.
                # Any other failure (403 block, network) must surface, not look empty.
                if page == 0:
                    return []
                raise
            page_events = data.get("events", [])
            if not page_events:
                break
            events.extend(page_events)
            if not data.get("hasNextPage"):
                break
            page += 1
            if page >= MAX_EVENT_PAGES:
                LOGGER.warning(
                    "Stopped paging %s events of season %s after %s pages",
                    direction,
                    season_id,
                    MAX_EVENT_PAGES,
                )
                break
        return events

    def get_remaining_fixtures(
        self, tournament_id: int, season_id: int
    ) -> list[dict[str, Any]]:
        """Return not-started fixtures for a season."""
        events = self._collect_events(tournament_id, season_id, "next")
        fixtures = [
            self._event_to_fixture(event)
            for event in events
            if event.get("status", {}).get("type") == "notstarted"
        ]
        LOGGER.info("Found %s remaining fixtures", len(fixtures))
        return fixtures

    def get_completed_events(
        self, tournament_id: int, season_id: int
    ) -> list[dict[str, Any]]:
        """Return finished events for a season."""
        events = self._collect_events(tournament_id, season_id, "last")
        completed = [
            event
            for event in events
            if event.get("status", {}).get("type") == "finished"
        ]
        completed.sort(key=lambda event: event.get("startTimestamp", 0))
        return completed

    def get_season_metadata(self, tournament_id: int, season_id: int) -> dict[str, Any]:
        """Fetch a small metadata snapshot for a season."""
        seasons = self.get_seasons(tournament_id)
        season = next(
            (season for season in seasons if season.get("id") == season_id), None
        )
        league_name = season.get("name") if season else f"Tournament {tournament_id}"
        return {
            "tournament_id": tournament_id,
            "season_id": season_id,
            "season": season,
            "league_name": league_name,
        }

    def build_snapshot_from_cutoff(
        self,
        tournament_id: int,
        season_id: int,
        *,
        matchday_cutoff: int | None = None,
        completed_match_count: int | None = None,
    ) -> ReplaySnapshot:
        """Build standings and remaining fixtures from completed historical events."""
        metadata = self.get_season_metadata(tournament_id, season_id)
        all_completed = self.get_completed_events(tournament_id, season_id)
        all_upcoming = self.get_remaining_fixtures(tournament_id, season_id)

        if matchday_cutoff is None and completed_match_count is None:
            raise ValueError(
                "Backtest snapshot requires matchday_cutoff or completed_match_count"
            )

        if matchday_cutoff is not None:
            completed_subset = [
                event
                for event in all_completed
                if event.get("roundInfo", {}).get("round") <= matchday_cutoff
            ]
            future_finished = [
                event
                for event in all_completed
                if event.get("roundInfo", {}).get("round") > matchday_cutoff
            ]
            label = f"matchday-{matchday_cutoff}"
        else:
            completed_subset = all_completed[:completed_match_count]
            future_finished = all_completed[completed_match_count:]
            label = f"after-{completed_match_count}-matches"

        season_total = self.get_league_table(tournament_id, season_id)
        if not season_total:
            raise RuntimeError("Could not load standings to infer teams")

        team_stats = team_stats_from_table(
            [
                TEAM_ROW_HEADER.copy(),
                *[[row[0], 0, 0, 0, 0, 0, 0, 0] for row in season_total[1:]],
            ]
        )
        home_stats = team_stats_from_table(
            [
                TEAM_ROW_HEADER.copy(),
                *[[row[0], 0, 0, 0, 0, 0, 0, 0] for row in season_total[1:]],
            ]
        )
        away_stats = team_stats_from_table(
            [
                TEAM_ROW_HEADER.copy(),
                *[[row[0], 0, 0, 0, 0, 0, 0, 0] for row in season_total[1:]],
            ]
        )

        for event in completed_subset:
            home_team = event["homeTeam"]["name"]
            away_team = event["awayTeam"]["name"]
            home_goals = int(event.get("homeScore", {}).get("current", 0))
            away_goals = int(event.get("awayScore", {}).get("current", 0))

            apply_result_to_split_tables(
                team_stats,
                home_stats,
                away_stats,
                home_team,
                away_team,
                home_goals,
                away_goals,
            )

        future_fixtures = [
            self._event_to_fixture(event) for event in future_finished
        ] + all_upcoming
        future_fixtures.sort(key=lambda fixture: fixture.get("datetime") or 0)

        team_colors = self.get_team_colors(tournament_id, season_id)
        return ReplaySnapshot(
            tournament_id=tournament_id,
            season_id=season_id,
            league_name=metadata["league_name"],
            snapshot_label=label,
            base_table=build_table_from_team_stats(team_stats),
            home_table=build_table_from_team_stats(home_stats),
            away_table=build_table_from_team_stats(away_stats),
            fixtures=future_fixtures,
            team_colors=team_colors,
            metadata={
                **metadata,
                "snapshot_label": label,
                "completed_events_used": len(completed_subset),
                "remaining_fixtures": len(future_fixtures),
                "cutoff_matchday": matchday_cutoff,
                "cutoff_completed_matches": completed_match_count,
            },
        )

    def export_snapshot(self, snapshot: ReplaySnapshot, destination: Path) -> Path:
        """Persist a replay snapshot to disk."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "tournament_id": snapshot.tournament_id,
            "season_id": snapshot.season_id,
            "league_name": snapshot.league_name,
            "snapshot_label": snapshot.snapshot_label,
            "base_table": snapshot.base_table,
            "home_table": snapshot.home_table,
            "away_table": snapshot.away_table,
            "fixtures": snapshot.fixtures,
            "team_colors": snapshot.team_colors,
            "metadata": snapshot.metadata,
        }
        destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return destination

    def load_snapshot(self, snapshot_path: Path) -> ReplaySnapshot:
        """Load a replay snapshot from disk."""
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return ReplaySnapshot(
            tournament_id=payload["tournament_id"],
            season_id=payload["season_id"],
            league_name=payload["league_name"],
            snapshot_label=payload["snapshot_label"],
            base_table=payload["base_table"],
            home_table=payload["home_table"],
            away_table=payload["away_table"],
            fixtures=payload["fixtures"],
            team_colors=payload["team_colors"],
            metadata=payload["metadata"],
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self.session.close()
