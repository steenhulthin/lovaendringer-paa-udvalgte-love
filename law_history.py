from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import re
import time
import urllib.error
import urllib.request


UNDERLYING_DATA_PATH = Path(__file__).with_name("underlying-data.md")
USER_AGENT = "luftfoto-guesser-law-dashboard/2.0"
MAX_METADATA_FETCH_WORKERS = 4
ELI_CACHE_TTL_SECONDS = 60 * 60 * 24
ELI_CACHE_DIR = Path(__file__).with_name(".cache") / "eli-rdfa"

CONSOLIDATED_DOCUMENT_TYPES = {"LBKH", "LOVH"}
CHANGE_DOCUMENT_TYPES = {"LOVC"}

AUTOLINK_PATTERN = re.compile(r"<(https://[^>]+)>")
LIST_ITEM_PATTERN = re.compile(r"^\s*\d+\.\s*(.+?)\s*$")


class LawDataError(RuntimeError):
    """Raised when official ELI metadata cannot be loaded."""


@dataclass(frozen=True)
class LawSource:
    slug: str
    name: str
    seed_urls: tuple[str, ...]


@dataclass(frozen=True)
class ChangeEvent:
    law_name: str
    source: str
    identifier: str
    law_number: int | None
    event_date: date
    title: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class EliMetadata:
    url: str
    document_type_code: str | None
    title: str | None
    title_alternative: str | None
    title_short: str | None
    date_document: date | None
    date_publication: date | None
    date_no_longer_in_force: date | None
    relevant_for_code: str | None
    changed_by: tuple[str, ...]
    consolidated_by: tuple[str, ...]
    consolidates: tuple[str, ...]
    basis_for: tuple[str, ...]

    @property
    def family_key(self) -> str:
        return derive_family_key(self)


LAW_SOURCES = tuple()


def load_law_sources_from_markdown(path: Path = UNDERLYING_DATA_PATH) -> tuple[LawSource, ...]:
    if not path.exists():
        raise LawDataError(f"Datagrundlaget blev ikke fundet: {path}")

    sources: list[LawSource] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line_match = LIST_ITEM_PATTERN.match(raw_line)
        if not line_match:
            continue

        line = line_match.group(1)
        urls = tuple(normalize_eli_url(url) for url in AUTOLINK_PATTERN.findall(line))
        if not urls:
            continue

        name = line.split("<", 1)[0].strip()
        sources.append(
            LawSource(
                slug=slugify(name),
                name=name,
                seed_urls=urls,
            )
        )

    if not sources:
        raise LawDataError(f"Kunne ikke udlede lovkilder fra {path}")

    return tuple(sources)


def load_law_history(source: LawSource) -> dict:
    versions = discover_versions(source)
    events = build_change_events(source.name, versions)
    counts = Counter(event.event_date.year for event in events)

    return {
        "slug": source.slug,
        "name": source.name,
        "reference_url": source.seed_urls[0],
        "seed_urls": list(source.seed_urls),
        "sources_used": ["Retsinformation ELI RDFa"],
        "warnings": [],
        "counts_by_year": dict(sorted(counts.items())),
        "events": [serialize_event(event) for event in events],
        "versions": [serialize_version(meta) for meta in versions],
    }


def discover_versions(source: LawSource) -> list[EliMetadata]:
    seed_metas = [fetch_eli_metadata(url) for url in source.seed_urls]
    family_key = choose_primary_family_key(seed_metas)
    relevant_for_code = choose_primary_relevant_for(seed_metas)

    discovered: dict[str, EliMetadata] = {}
    chain_starts: dict[str, EliMetadata] = {}

    for meta in seed_metas:
        start = trace_to_edge(
            meta,
            family_key=family_key,
            relevant_for_code=relevant_for_code,
            direction="previous",
        )
        chain_starts[start.url] = start

    for start in chain_starts.values():
        current: EliMetadata | None = start
        local_seen: set[str] = set()

        while current and current.url not in local_seen:
            local_seen.add(current.url)
            discovered[current.url] = current
            current = find_adjacent_version(
                current,
                relation_urls=current.consolidated_by,
                family_key=family_key,
                relevant_for_code=relevant_for_code,
                direction="next",
            )

    for seed_meta in seed_metas:
        if seed_meta.url in discovered:
            continue

        current: EliMetadata | None = seed_meta
        local_seen: set[str] = set()
        while current and current.url not in local_seen:
            local_seen.add(current.url)
            discovered[current.url] = current
            current = find_adjacent_version(
                current,
                relation_urls=current.consolidated_by,
                family_key=family_key,
                relevant_for_code=relevant_for_code,
                direction="next",
            )

    return sorted(
        discovered.values(),
        key=lambda item: (
            item.date_document or date.min,
            item.title_short or "",
            item.url,
        ),
    )


def build_change_events(law_name: str, versions: list[EliMetadata]) -> list[ChangeEvent]:
    change_urls: set[str] = set()

    for version in versions:
        change_urls.update(collect_change_urls(version))

    events: list[ChangeEvent] = []

    for change_meta in fetch_many_eli_metadata(sorted(change_urls)):
        event_date = change_meta.date_document or change_meta.date_publication
        if event_date is None:
            continue

        events.append(
            ChangeEvent(
                law_name=law_name,
                source="eli_rdfa",
                identifier=change_meta.url,
                law_number=extract_number_from_eli_url(change_meta.url),
                event_date=event_date,
                title=change_meta.title_short or change_meta.title,
                url=change_meta.url,
            )
        )

    return sorted(events, key=lambda item: (item.event_date, item.law_number or 0))


def collect_change_urls(version: EliMetadata) -> set[str]:
    return set(collect_change_urls_for_version(version.url))


@lru_cache(maxsize=None)
def collect_change_urls_for_version(version_url: str) -> frozenset[str]:
    version = fetch_eli_metadata(version_url)
    change_urls: set[str] = set()

    for linked_meta in fetch_many_eli_metadata(version.changed_by):
        if is_change_act(linked_meta):
            change_urls.add(linked_meta.url)

    return frozenset(change_urls)


def trace_to_edge(
    meta: EliMetadata,
    *,
    family_key: str,
    relevant_for_code: str | None,
    direction: str,
) -> EliMetadata:
    current = meta
    seen = {meta.url}

    while True:
        relation_urls = (
            current.consolidates if direction == "previous" else current.consolidated_by
        )
        neighbor = find_adjacent_version(
            current,
            relation_urls=relation_urls,
            family_key=family_key,
            relevant_for_code=relevant_for_code,
            direction=direction,
        )
        if neighbor is None or neighbor.url in seen:
            return current

        seen.add(neighbor.url)
        current = neighbor


def find_adjacent_version(
    current: EliMetadata,
    *,
    relation_urls: tuple[str, ...],
    family_key: str,
    relevant_for_code: str | None,
    direction: str,
) -> EliMetadata | None:
    candidates: list[EliMetadata] = []

    for candidate in fetch_many_eli_metadata(relation_urls):
        if candidate.url == current.url:
            continue
        if not is_consolidated_version(candidate):
            continue
        if not belongs_to_family(candidate, family_key, relevant_for_code):
            continue
        if candidate.date_document is None or current.date_document is None:
            continue

        if direction == "previous" and candidate.date_document < current.date_document:
            candidates.append(candidate)
        if direction == "next" and candidate.date_document > current.date_document:
            candidates.append(candidate)

    if not candidates:
        return None

    if direction == "previous":
        return max(candidates, key=lambda item: (item.date_document, item.url))

    return min(candidates, key=lambda item: (item.date_document, item.url))


def fetch_many_eli_metadata(urls: tuple[str, ...] | list[str]) -> list[EliMetadata]:
    normalized_urls: list[str] = []
    seen_urls: set[str] = set()

    for url in urls:
        if not is_lta_resource(url):
            continue

        normalized_url = normalize_eli_url(url)
        if normalized_url in seen_urls:
            continue

        seen_urls.add(normalized_url)
        normalized_urls.append(normalized_url)

    if not normalized_urls:
        return []

    if len(normalized_urls) == 1:
        return [fetch_eli_metadata(normalized_urls[0])]

    worker_count = min(MAX_METADATA_FETCH_WORKERS, len(normalized_urls))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        return list(executor.map(fetch_eli_metadata, normalized_urls))


def belongs_to_family(
    candidate: EliMetadata,
    family_key: str,
    relevant_for_code: str | None,
) -> bool:
    if family_key and candidate.family_key != family_key:
        return False

    if (
        relevant_for_code
        and candidate.relevant_for_code
        and candidate.relevant_for_code != relevant_for_code
    ):
        return False

    return True


def choose_primary_family_key(seed_metas: list[EliMetadata]) -> str:
    for meta in seed_metas:
        if meta.family_key:
            return meta.family_key
    return ""


def choose_primary_relevant_for(seed_metas: list[EliMetadata]) -> str | None:
    for meta in seed_metas:
        if meta.relevant_for_code:
            return meta.relevant_for_code
    return None


@lru_cache(maxsize=None)
def fetch_eli_metadata(url: str) -> EliMetadata:
    base_url = normalize_eli_url(url)
    rdfa_url = f"{base_url}.rdfa"
    cached_items = load_cached_rdfa(base_url)

    if cached_items is not None:
        return parse_eli_metadata(cached_items, base_url)

    try:
        with open_url(rdfa_url) as response:
            items = json.load(response)
    except urllib.error.URLError as exc:
        stale_items = load_cached_rdfa(base_url, allow_stale=True)
        if stale_items is not None:
            return parse_eli_metadata(stale_items, base_url)
        raise LawDataError(f"Kunne ikke hente ELI-metadata fra {rdfa_url}") from exc
    except json.JSONDecodeError as exc:
        raise LawDataError(f"Kunne ikke læse ELI-metadata fra {rdfa_url}") from exc

    save_cached_rdfa(base_url, items)
    return parse_eli_metadata(items, base_url)


def parse_eli_metadata(items: list[dict], base_url: str) -> EliMetadata:
    properties = collect_properties(items)

    return EliMetadata(
        url=base_url,
        document_type_code=extract_authority_code(first_value(properties, "eli:type_document")),
        title=first_value(properties, "eli:title"),
        title_alternative=first_value(properties, "eli:title_alternative"),
        title_short=first_value(properties, "eli:title_short"),
        date_document=parse_iso_date(first_value(properties, "eli:date_document")),
        date_publication=parse_iso_date(first_value(properties, "eli:date_publication")),
        date_no_longer_in_force=parse_iso_date(
            first_value(properties, "eli:date_no_longer_in_force")
        ),
        relevant_for_code=extract_authority_code(first_value(properties, "eli:relevant_for")),
        changed_by=tuple(properties.get("eli:changed_by", [])),
        consolidated_by=tuple(properties.get("eli:consolidated_by", [])),
        consolidates=tuple(properties.get("eli:consolidates", [])),
        basis_for=tuple(properties.get("eli:basis_for", [])),
    )


def load_cached_rdfa(base_url: str, *, allow_stale: bool = False) -> list[dict] | None:
    cache_path = cache_path_for_url(base_url)
    if not cache_path.exists():
        return None

    try:
        if not allow_stale:
            age_seconds = time.time() - cache_path.stat().st_mtime
            if age_seconds > ELI_CACHE_TTL_SECONDS:
                return None
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_cached_rdfa(base_url: str, items: list[dict]) -> None:
    cache_path = cache_path_for_url(base_url)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(items), encoding="utf-8")
    temp_path.replace(cache_path)


def cache_path_for_url(base_url: str) -> Path:
    cache_key = hashlib.sha256(base_url.encode("utf-8")).hexdigest()
    return ELI_CACHE_DIR / f"{cache_key}.json"


def collect_properties(items: list[dict]) -> dict[str, list[str]]:
    properties: dict[str, list[str]] = {}

    for item in items:
        prop = item.get("property")
        if not prop:
            continue

        value = item.get("resource") or item.get("content")
        if value is None:
            continue

        properties.setdefault(prop, []).append(str(value))

    return properties


def derive_family_key(meta: EliMetadata) -> str:
    for candidate in (meta.title_alternative, meta.title, meta.title_short):
        normalized = normalize_law_title(candidate)
        if normalized:
            return normalized
    return ""


def normalize_law_title(value: str | None) -> str:
    if not value:
        return ""

    normalized = value.lower().strip()
    normalized = re.sub(r"\([^)]*\)", "", normalized)
    normalized = normalized.replace("  ", " ")

    for prefix in (
        "bekendtgørelse af lov om ",
        "bekendtgørelse af ",
        "lov om ",
    ):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break

    normalized = normalized.strip()
    if normalized.endswith("loven"):
        normalized = normalized[: -len("loven")] + "lov"

    normalized = re.sub(r"[^0-9a-zæøå]+", "", normalized)
    return normalized


def is_consolidated_version(meta: EliMetadata) -> bool:
    return meta.document_type_code in CONSOLIDATED_DOCUMENT_TYPES


def is_change_act(meta: EliMetadata) -> bool:
    title = (meta.title or "").lower().strip()
    return meta.document_type_code in CHANGE_DOCUMENT_TYPES or title.startswith(
        "lov om ændring af"
    )


def serialize_event(event: ChangeEvent) -> dict:
    return {
        "law_name": event.law_name,
        "source": event.source,
        "identifier": event.identifier,
        "law_number": event.law_number,
        "event_date": event.event_date.isoformat(),
        "title": event.title,
        "url": event.url,
    }


def serialize_version(meta: EliMetadata) -> dict:
    return {
        "url": meta.url,
        "document_type_code": meta.document_type_code,
        "title": meta.title,
        "title_alternative": meta.title_alternative,
        "title_short": meta.title_short,
        "date_document": meta.date_document.isoformat() if meta.date_document else None,
        "date_publication": meta.date_publication.isoformat() if meta.date_publication else None,
        "date_no_longer_in_force": (
            meta.date_no_longer_in_force.isoformat()
            if meta.date_no_longer_in_force
            else None
        ),
    }


def build_year_rows(histories: list[dict]) -> list[dict]:
    all_years = sorted(
        year
        for history in histories
        for year in history.get("counts_by_year", {}).keys()
    )
    if not all_years:
        return []

    start_year = min(all_years)
    end_year = max(all_years)
    rows: list[dict] = []

    for history in histories:
        counts = history.get("counts_by_year", {})
        for year in range(start_year, end_year + 1):
            rows.append(
                {
                    "Lov": history["name"],
                    "År": year,
                    "Ændringer": counts.get(year, 0),
                }
            )

    return rows


def build_event_rows(histories: list[dict]) -> list[dict]:
    rows: list[dict] = []

    for history in histories:
        for event in history.get("events", []):
            rows.append(
                {
                    "Lov": history["name"],
                    "Dato": event["event_date"],
                    "År": int(event["event_date"][:4]),
                    "Lov nr.": event["law_number"],
                    "Kilde": event["source"],
                    "Titel": event["title"],
                    "URL": event["url"],
                }
            )

    return sorted(rows, key=lambda row: row["Dato"], reverse=True)


def first_value(properties: dict[str, list[str]], key: str) -> str | None:
    values = properties.get(key)
    if not values:
        return None
    return values[0]


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def extract_authority_code(value: str | None) -> str | None:
    if not value or "#" not in value:
        return None
    return value.rsplit("#", 1)[-1]


def extract_number_from_eli_url(url: str) -> int | None:
    match = re.search(r"/eli/lta/\d{4}/(\d+)$", normalize_eli_url(url))
    if not match:
        return None
    return int(match.group(1))


def normalize_eli_url(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith(".rdfa"):
        normalized = normalized[: -len(".rdfa")]
    return normalized


def is_lta_resource(url: str) -> bool:
    return "/eli/lta/" in normalize_eli_url(url)


def slugify(value: str) -> str:
    normalized = (
        value.lower()
        .replace("æ", "ae")
        .replace("ø", "oe")
        .replace("å", "aa")
    )
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    return normalized.strip("-")


def open_url(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=30)


LAW_SOURCES = load_law_sources_from_markdown()
