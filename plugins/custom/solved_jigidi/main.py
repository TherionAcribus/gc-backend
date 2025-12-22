"""Plugin Solved Jigidi pour MysterAI.

Ce plugin recherche un code GC dans le Google Sheet public "Solved Jigidi" et retourne
les coordonnées finales (DDM + décimal) ainsi que les notes associées.

Fonctionnalités clés:
- Cache en mémoire + stratégie "stale-while-revalidate" (retour immédiat sur cache,
  refresh en tâche de fond si le cache est vieux)
- Snapshot local (backup) utilisé si le téléchargement échoue
"""

from __future__ import annotations

import csv
import json
import re
import threading
import time
import html as html_lib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

try:  # pragma: no cover
    import requests
except Exception as import_error:  # noqa: F401
    requests = None  # type: ignore
    REQUESTS_IMPORT_ERROR = import_error
else:
    REQUESTS_IMPORT_ERROR = None


_SHEET_PUBLISHED_ID = (
    "2PACX-1vQ358lFBRDaOUD1GOOvhOR9Wp4ECnUINbgT5M_vqiRTGoM3k3OKtY2shq1Ajqsmf7T8XqIE7Owm0-z4"
)

_PUBHTML_URL = f"https://docs.google.com/spreadsheets/d/e/{_SHEET_PUBLISHED_ID}/pubhtml"

_CACHE_FORMAT_VERSION = 2

_SITE_LOCK = threading.Lock()
_SITE_LAST_REQUEST_AT = 0.0
_SITE_COOLDOWN_UNTIL = 0.0
_SITE_MIN_INTERVAL_SECONDS = 1.0
_SITE_COOLDOWN_SECONDS_ON_429 = 120
_SITE_RATE_LIMIT_CACHE_SECONDS = 300


class _SolvedJigidiSiteRateLimited(Exception):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _CacheState:
    index_by_code: Dict[str, Dict[str, Any]]
    loaded_at: Optional[datetime]
    refreshing: bool
    last_refresh_error: Optional[str]
    source: str


_CACHE_LOCK = threading.RLock()
_CACHE = _CacheState(
    index_by_code={},
    loaded_at=None,
    refreshing=False,
    last_refresh_error=None,
    source="empty",
)


class SolvedJigidiPlugin:
    def __init__(self) -> None:
        self.name = "solved_jigidi"
        self.version = "1.0.0"

        self._base_dir = Path(__file__).resolve().parent
        self._snapshot_path = self._base_dir / "snapshot.json"
        self._csv_cache_path = self._base_dir / "sheet_cache.csv"
        self._csv_cache_meta_path = self._base_dir / "sheet_cache_meta.json"
        self._site_cache_path = self._base_dir / "site_cache.json"

        self._session = None
        if requests is not None:
            self._session = requests.Session()
            self._session.headers.setdefault(
                "User-Agent",
                "GeoApp/1.0 (SolvedJigidiPlugin; +https://example.local)",
            )

        self._load_snapshot_if_available()
        self._load_disk_csv_cache_if_available()

    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Point d'entrée principal du plugin.

        Args:
            inputs: paramètres d'entrée du plugin.

        Returns:
            Résultat au format standardisé MysterAI.
        """
        start_time = time.time()

        if REQUESTS_IMPORT_ERROR is not None or self._session is None:
            return self._error(
                start_time,
                "Dépendance manquante: requests",
                details=str(REQUESTS_IMPORT_ERROR),
            )

        gc_code = self._resolve_gc_code(inputs)
        if not gc_code:
            return self._error(
                start_time,
                "Impossible de déterminer le code GC (fournis gc_code ou geocache_id)",
            )

        max_age_hours = self._parse_max_age_hours(inputs.get("max_age_hours"), default=24)
        force_refresh = bool(inputs.get("force_refresh", False))
        backup_url = self._normalize_backup_url(inputs.get("backup_url"))
        site_fallback = bool(inputs.get("site_fallback", True))
        site_cache_ttl_hours = self._parse_hours(inputs.get("site_cache_ttl_hours"), default=168, max_value=720)

        cache_status = self._ensure_cache_swr(
            max_age_hours=max_age_hours,
            force_refresh=force_refresh,
            backup_url=backup_url,
        )

        row = None
        with _CACHE_LOCK:
            row = _CACHE.index_by_code.get(gc_code)

        if not row:
            if self._should_try_refresh_when_missing(cache_status):
                self._trigger_background_refresh_if_possible(backup_url)

            if site_fallback:
                site_retry_after_seconds = self._get_site_retry_after_seconds(gc_code)
                if site_retry_after_seconds <= 0:
                    site_row = self._lookup_site_cached(gc_code, ttl_hours=site_cache_ttl_hours)
                else:
                    site_row = None
                if site_row:
                    return self._build_result_from_row(
                        start_time=start_time,
                        gc_code=gc_code,
                        row=site_row,
                        cache_status=f"{cache_status}+site",
                        backup_url=backup_url,
                    )

                site_retry_after_seconds = self._get_site_retry_after_seconds(gc_code)
            else:
                site_retry_after_seconds = 0

            if site_retry_after_seconds > 0:
                summary = (
                    "solvedjigidi.com semble saturé (HTTP 429 / limitation de débit). "
                    f"Merci de réessayer plus tard (dans ~{site_retry_after_seconds}s). "
                    f"Cache={cache_status}."
                )
                return {
                    "status": "ok",
                    "summary": summary,
                    "results": [],
                    "plugin_info": self._build_plugin_info(start_time, cache_status),
                    "metadata": {
                        "gc_code": gc_code,
                        "cache_status": cache_status,
                        "cache_refreshing": bool(_CACHE.refreshing),
                        "backup_url": backup_url,
                        "site_fallback": site_fallback,
                        "site_cache_ttl_hours": site_cache_ttl_hours,
                        "site_rate_limited": True,
                        "site_retry_after_seconds": int(site_retry_after_seconds),
                    },
                }

            if cache_status == "loading":
                summary = (
                    f"Chargement initial des données SolvedJigidi en cours (premier démarrage). "
                    f"Cela peut prendre un peu de temps. "
                    f"Réessaie dans quelques secondes. Cache={cache_status}."
                )
            else:
                summary = (
                    f"Aucune entrée SolvedJigidi trouvée pour {gc_code}. "
                    f"Cache={cache_status}."
                )
            return {
                "status": "ok",
                "summary": summary,
                "results": [],
                "plugin_info": self._build_plugin_info(start_time, cache_status),
                "metadata": {
                    "gc_code": gc_code,
                    "cache_status": cache_status,
                    "cache_refreshing": bool(_CACHE.refreshing),
                    "backup_url": backup_url,
                    "site_fallback": site_fallback,
                    "site_cache_ttl_hours": site_cache_ttl_hours,
                    "site_rate_limited": False,
                    "site_retry_after_seconds": 0,
                },
            }

        return self._build_result_from_row(
            start_time=start_time,
            gc_code=gc_code,
            row=row,
            cache_status=cache_status,
            backup_url=backup_url,
        )

    def _build_result_from_row(
        self,
        start_time: float,
        gc_code: str,
        row: Dict[str, Any],
        cache_status: str,
        backup_url: Optional[str],
    ) -> Dict[str, Any]:
        try:
            from gc_backend.blueprints.coordinates import detect_gps_coordinates  # type: ignore
        except Exception as exc:  # pragma: no cover
            detect_gps_coordinates = None  # type: ignore
            logger.warning(f"detect_gps_coordinates indisponible: {exc}")

        lat_ddm = (row.get("Latitude") or "").strip()
        lon_ddm = (row.get("Longitude") or "").strip()
        coord_text = f"{lat_ddm} {lon_ddm}".strip()

        detection = None
        if detect_gps_coordinates and coord_text:
            try:
                detection = detect_gps_coordinates(coord_text)
            except Exception as exc:  # pragma: no cover
                logger.warning(f"Erreur detect_gps_coordinates({gc_code}): {exc}")
                detection = None

        description = (row.get("Description") or "").strip()
        country = (row.get("Country") or "").strip()
        state = (row.get("State") or "").strip()
        user_notes = (row.get("UserNotes") or "").strip()
        date_added = (row.get("Date Added") or "").strip()

        text_lines = [f"{gc_code} — {description}".strip(" —")]
        if coord_text:
            text_lines.append(f"Coordonnées: {coord_text}")
        if user_notes:
            text_lines.append(f"Notes: {user_notes}")
        if country or state:
            text_lines.append(f"Zone: {country} {state}".strip())
        if date_added:
            text_lines.append(f"Date ajout: {date_added}")
        source = row.get("_source", "")
        if source in {"solvedjigidi_site", "solvedjigidi_site_cache"}:
            text_lines.append("")
            text_lines.append("Source: solvedjigidi.com (https://solvedjigidi.com/)")

        result_item: Dict[str, Any] = {
            "id": f"solved_jigidi_{gc_code}",
            "text_output": "\n".join([line for line in text_lines if line]),
            "confidence": 0.95,
            "metadata": {
                "gc_code": gc_code,
                "description": description,
                "country": country,
                "state": state,
                "user_notes": user_notes,
                "date_added": date_added,
                "source": row.get("_source"),
                "sheet_gid": row.get("_sheet_gid"),
                "sheet_name": row.get("_sheet_name"),
            },
        }

        if detection and isinstance(detection, dict) and detection.get("exist"):
            formatted = detection.get("formatted")
            if not formatted:
                formatted = detection.get("ddm")
            if not formatted:
                ddm_lat = detection.get("ddm_lat")
                ddm_lon = detection.get("ddm_lon")
                if ddm_lat and ddm_lon:
                    formatted = f"{ddm_lat} {ddm_lon}".strip()
            if formatted:
                detection["formatted"] = formatted
            result_item["coordinates"] = detection
            if detection.get("decimal_latitude") is not None:
                result_item["decimal_latitude"] = detection.get("decimal_latitude")
            if detection.get("decimal_longitude") is not None:
                result_item["decimal_longitude"] = detection.get("decimal_longitude")

        top_level_coords = None
        if (
            isinstance(result_item.get("decimal_latitude"), (int, float))
            and isinstance(result_item.get("decimal_longitude"), (int, float))
        ):
            top_level_coords = {
                "latitude": float(result_item["decimal_latitude"]),
                "longitude": float(result_item["decimal_longitude"]),
            }

        primary_coordinates = None
        if isinstance(top_level_coords, dict):
            primary_coordinates = dict(top_level_coords)
            if detection and isinstance(detection, dict) and detection.get("formatted"):
                primary_coordinates["formatted"] = detection.get("formatted")

        summary = f"Entrée SolvedJigidi trouvée pour {gc_code}. Cache={cache_status}."

        return {
            "status": "ok",
            "summary": summary,
            "results": [result_item],
            "coordinates": top_level_coords,
            "primary_coordinates": primary_coordinates,
            "plugin_info": self._build_plugin_info(start_time, cache_status),
            "metadata": {
                "gc_code": gc_code,
                "cache_status": cache_status,
                "cache_loaded_at": _CACHE.loaded_at.isoformat() if _CACHE.loaded_at else None,
                "cache_source": _CACHE.source,
                "last_refresh_error": _CACHE.last_refresh_error,
                "backup_url": backup_url,
                "source_url": "https://solvedjigidi.com/",
            },
        }

    def _parse_hours(self, value: Any, default: int, max_value: int) -> int:
        try:
            if value is None:
                return default
            parsed = int(float(value))
            if parsed < 1:
                return 1
            if parsed > max_value:
                return max_value
            return parsed
        except Exception:
            return default

    def _should_try_refresh_when_missing(self, cache_status: str) -> bool:
        with _CACHE_LOCK:
            source = _CACHE.source
        if source != "google_sheet" and cache_status in {"fresh", "loaded"}:
            return True
        return False

    def _trigger_background_refresh_if_possible(self, backup_url: Optional[str]) -> None:
        with _CACHE_LOCK:
            if _CACHE.refreshing:
                return
            _CACHE.refreshing = True

        threading.Thread(
            target=self._refresh_cache_background,
            args=(backup_url,),
            name="SolvedJigidiRefreshMissing",
            daemon=True,
        ).start()

    def _lookup_site_cached(self, gc_code: str, ttl_hours: int) -> Optional[Dict[str, Any]]:
        cached = self._read_site_cache()
        entry = cached.get(gc_code)
        if isinstance(entry, dict):
            fetched_at = entry.get("fetched_at")
            data = entry.get("data")
            error = entry.get("error")
            if isinstance(fetched_at, str) and isinstance(error, str) and error:
                try:
                    dt = datetime.fromisoformat(fetched_at)
                    age = (_utc_now() - dt).total_seconds()
                    if error == "not_found" and age <= ttl_hours * 3600:
                        return None
                    if error == "rate_limited" and age <= _SITE_RATE_LIMIT_CACHE_SECONDS:
                        return None
                except Exception:
                    pass

            if isinstance(fetched_at, str) and isinstance(data, dict):
                try:
                    dt = datetime.fromisoformat(fetched_at)
                    age = (_utc_now() - dt).total_seconds()
                    if age <= ttl_hours * 3600:
                        data = dict(data)
                        data["_source"] = "solvedjigidi_site_cache"
                        return data
                except Exception:
                    pass

        try:
            row = self._lookup_site(gc_code)
        except _SolvedJigidiSiteRateLimited:
            cached[gc_code] = {"fetched_at": _utc_now().isoformat(), "error": "rate_limited"}
            self._write_site_cache(cached)
            return None
        except Exception as exc:
            logger.warning(f"Lookup solvedjigidi.com échoué pour {gc_code}: {exc}")
            return None

        if row is None:
            cached[gc_code] = {"fetched_at": _utc_now().isoformat(), "error": "not_found"}
            self._write_site_cache(cached)
            return None

        cached[gc_code] = {"fetched_at": _utc_now().isoformat(), "data": row}
        self._write_site_cache(cached)
        row = dict(row)
        row["_source"] = "solvedjigidi_site"
        return row

    def _read_site_cache(self) -> Dict[str, Any]:
        try:
            if not self._site_cache_path.exists():
                return {}
            raw = self._site_cache_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_site_cache(self, data: Dict[str, Any]) -> None:
        try:
            tmp = self._site_cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self._site_cache_path)
        except Exception as exc:
            logger.warning(f"Impossible d'écrire le cache site SolvedJigidi: {exc}")

    def _lookup_site(self, gc_code: str) -> Optional[Dict[str, Any]]:
        url = f"https://solvedjigidi.com/search.php?gc={gc_code}"

        with _SITE_LOCK:
            now = time.time()
            if now < _SITE_COOLDOWN_UNTIL:
                raise _SolvedJigidiSiteRateLimited("Cooldown actif")

            wait_for = (_SITE_LAST_REQUEST_AT + _SITE_MIN_INTERVAL_SECONDS) - now
            if wait_for > 0:
                time.sleep(wait_for)

            # Réévaluer maintenant après le sleep
            now = time.time()
            if now < _SITE_COOLDOWN_UNTIL:
                raise _SolvedJigidiSiteRateLimited("Cooldown actif")

            resp = self._session.get(url, timeout=20)
            globals()["_SITE_LAST_REQUEST_AT"] = time.time()

            if getattr(resp, "status_code", None) == 429:
                globals()["_SITE_COOLDOWN_UNTIL"] = time.time() + _SITE_COOLDOWN_SECONDS_ON_429
                raise _SolvedJigidiSiteRateLimited("429 Too Many Requests")

            resp.raise_for_status()
        html = resp.content.decode("utf-8", errors="replace")

        # Certains rate-limits sont renvoyés en HTTP 200 avec une page HTML dédiée.
        # Exemple: "Too many requests in a short time! ... Please try again later."
        if re.search(r"too\s+many\s+requests\s+in\s+a\s+short\s+time", html, flags=re.IGNORECASE):
            globals()["_SITE_COOLDOWN_UNTIL"] = time.time() + _SITE_COOLDOWN_SECONDS_ON_429
            raise _SolvedJigidiSiteRateLimited("Rate limited HTML page")

        if "Found" not in html and "<h3>Found" not in html:
            return None

        def extract_field(label: str) -> str:
            pattern = rf"<strong>{re.escape(label)}:</strong>\s*(.*?)</p>"
            match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
            if not match:
                return ""
            value_raw = match.group(1)
            value_raw = re.sub(r"<[^>]+>", " ", value_raw)
            value_raw = html_lib.unescape(value_raw)
            return re.sub(r"\s+", " ", value_raw).strip()

        coords_text = ""
        coords_match = re.search(r"copyText\('([^']+)'\)", html)
        if coords_match:
            coords_text = html_lib.unescape(coords_match.group(1)).strip()
        else:
            coords_text = extract_field("Coords")

        lat = ""
        lon = ""
        if coords_text:
            m = re.search(r"([NS].*?[0-9])\s*,\s*([EW].*?[0-9])", coords_text)
            if m:
                lat = m.group(1).strip()
                lon = m.group(2).strip()

        if not lat or not lon:
            return None

        data: Dict[str, Any] = {
            "Code": gc_code,
            "Description": extract_field("Name"),
            "Latitude": lat,
            "Longitude": lon,
            "Country": extract_field("Country"),
            "State": extract_field("State"),
            "UserNotes": extract_field("Notes"),
            "Date Added": extract_field("Date added/updated"),
        }
        return data

    def _get_site_retry_after_seconds(self, gc_code: Optional[str] = None) -> int:
        now = time.time()
        retry_seconds = 0

        try:
            cooldown_until = float(globals().get("_SITE_COOLDOWN_UNTIL", 0.0))
        except Exception:
            cooldown_until = 0.0

        if now < cooldown_until:
            retry_seconds = max(retry_seconds, int(cooldown_until - now))

        if gc_code:
            try:
                cached = self._read_site_cache()
                entry = cached.get(gc_code)
                if isinstance(entry, dict) and entry.get("error") == "rate_limited":
                    fetched_at = entry.get("fetched_at")
                    if isinstance(fetched_at, str) and fetched_at:
                        dt = datetime.fromisoformat(fetched_at)
                        age = (_utc_now() - dt).total_seconds()
                        if age <= _SITE_RATE_LIMIT_CACHE_SECONDS:
                            retry_seconds = max(
                                retry_seconds,
                                int(_SITE_RATE_LIMIT_CACHE_SECONDS - age),
                            )
            except Exception:
                pass

        return retry_seconds

    def _normalize_backup_url(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        url = str(value).strip()
        if not url:
            return None
        return url

    def _normalize_gc_code_key(self, value: Any) -> str:
        if value is None:
            return ""
        code = str(value).strip().upper()
        code = re.sub(r"\s+", "", code)
        return code

    def _resolve_gc_code(self, inputs: Dict[str, Any]) -> Optional[str]:
        raw = inputs.get("gc_code")
        if raw is not None and str(raw).strip():
            return self._normalize_gc_code_key(raw)

        geocache_id_raw = inputs.get("geocache_id")
        if geocache_id_raw is None:
            return None

        try:
            from gc_backend.database import db
            from gc_backend.geocaches.models import Geocache
        except Exception:
            return None

        geocache_id_str = str(geocache_id_raw).strip()

        geocache = None
        try:
            geocache_id_int = int(geocache_id_str)
        except (TypeError, ValueError):
            geocache_id_int = None

        if geocache_id_int is not None:
            geocache = db.session.query(Geocache).get(geocache_id_int)

        if geocache is None:
            geocache = (
                db.session.query(Geocache)
                .filter(Geocache.gc_code == geocache_id_str.upper())
                .first()
            )

        if geocache and getattr(geocache, "gc_code", None):
            return self._normalize_gc_code_key(geocache.gc_code)

        return None

    def _parse_max_age_hours(self, value: Any, default: int) -> int:
        try:
            if value is None:
                return default
            parsed = int(float(value))
            if parsed < 1:
                return 1
            if parsed > 168:
                return 168
            return parsed
        except Exception:
            return default

    def _ensure_cache_swr(
        self,
        max_age_hours: int,
        force_refresh: bool,
        backup_url: Optional[str],
    ) -> str:
        """Assure un cache disponible et déclenche un refresh asynchrone si nécessaire."""
        with _CACHE_LOCK:
            cache_available = bool(_CACHE.index_by_code)
            cache_age_seconds = None
            if _CACHE.loaded_at is not None:
                cache_age_seconds = (_utc_now() - _CACHE.loaded_at).total_seconds()

            is_stale = (
                _CACHE.loaded_at is None
                or cache_age_seconds is None
                or cache_age_seconds > max_age_hours * 3600
            )

            if force_refresh and cache_available:
                is_stale = True

            if cache_available and not is_stale:
                return "fresh"

            if cache_available and is_stale:
                if not _CACHE.refreshing:
                    _CACHE.refreshing = True
                    threading.Thread(
                        target=self._refresh_cache_background,
                        args=(backup_url,),
                        name="SolvedJigidiRefresh",
                        daemon=True,
                    ).start()
                return "stale_refreshing"

        # Pas de cache en mémoire.
        # - Si force_refresh: on essaye un chargement synchrone.
        # - Sinon: on déclenche un refresh en tâche de fond et on répond immédiatement.
        if force_refresh:
            try:
                self._refresh_cache_sync(backup_url)
                return "loaded"
            except Exception as exc:
                logger.error(f"Impossible de charger le cache SolvedJigidi: {exc}")
                with _CACHE_LOCK:
                    if _CACHE.index_by_code:
                        return "stale_due_to_error"
                return "empty_error"

        with _CACHE_LOCK:
            if not _CACHE.refreshing:
                _CACHE.refreshing = True
                threading.Thread(
                    target=self._refresh_cache_background,
                    args=(backup_url,),
                    name="SolvedJigidiInitialRefresh",
                    daemon=True,
                ).start()
        return "loading"

    def _refresh_cache_background(self, backup_url: Optional[str]) -> None:
        try:
            self._refresh_cache_sync(backup_url)
        except Exception as exc:
            logger.error(f"Refresh background SolvedJigidi échoué: {exc}")
            with _CACHE_LOCK:
                _CACHE.last_refresh_error = str(exc)
        finally:
            with _CACHE_LOCK:
                _CACHE.refreshing = False

    def _refresh_cache_sync(self, backup_url: Optional[str]) -> None:
        index = self._download_and_build_index(backup_url)
        now = _utc_now()

        source = "unknown"
        try:
            any_row = next(iter(index.values()))
            if isinstance(any_row, dict) and any_row.get("_source"):
                source = str(any_row.get("_source"))
        except Exception:
            source = "unknown"

        with _CACHE_LOCK:
            _CACHE.index_by_code = index
            _CACHE.loaded_at = now
            _CACHE.last_refresh_error = None
            _CACHE.source = source

        self._persist_snapshot(index, loaded_at=now, source=source)
        self._persist_disk_csv_cache(index, loaded_at=now, source=source)

    def _download_and_build_index(self, backup_url: Optional[str]) -> Dict[str, Dict[str, Any]]:
        try:
            tabs = self._fetch_sheet_tabs()
            return self._download_and_merge_tabs(tabs)
        except Exception as exc:
            logger.warning(f"Téléchargement Google Sheet échoué: {exc}")
            if backup_url:
                logger.info("Tentative de fallback via backup_url...")
                return self._download_index_from_url(backup_url, source_label="backup_url")
            raise

    def _fetch_sheet_tabs(self) -> List[Tuple[str, str]]:
        resp = self._session.get(_PUBHTML_URL, timeout=20)
        resp.raise_for_status()
        html = resp.content.decode("utf-8", errors="replace")

        matches = re.findall(
            r'items\.push\(\{name: "([^"]+)", pageUrl: "[^"]+gid=([0-9-]+)"',
            html,
        )

        tabs: List[Tuple[str, str]] = []
        seen = set()
        for name, gid in matches:
            if gid in seen:
                continue
            seen.add(gid)
            tabs.append((gid, name))

        if not tabs:
            tabs = [("0", "Solved Jigidi")]

        return tabs

    def _download_and_merge_tabs(self, tabs: List[Tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
        index: Dict[str, Dict[str, Any]] = {}
        for gid, name in tabs:
            url = f"https://docs.google.com/spreadsheets/d/e/{_SHEET_PUBLISHED_ID}/pub?output=csv&gid={gid}"
            logger.info(f"Téléchargement SolvedJigidi CSV (gid={gid}, name={name}) ...")

            partial = self._download_index_from_url(url, source_label="google_sheet")
            for code, row in partial.items():
                row.setdefault("_sheet_gid", gid)
                row.setdefault("_sheet_name", name)

                existing = index.get(code)
                if existing is None or self._should_replace_row(existing, row):
                    index[code] = row

        if not index:
            raise ValueError("Aucune donnée chargée depuis les onglets (index vide)")

        logger.info(f"SolvedJigidi: {len(index)} entrées chargées (tabs={len(tabs)})")
        return index

    def _should_replace_row(self, existing: Dict[str, Any], new: Dict[str, Any]) -> bool:
        existing_has_coords = bool((existing.get("Latitude") or "").strip()) and bool(
            (existing.get("Longitude") or "").strip()
        )
        new_has_coords = bool((new.get("Latitude") or "").strip()) and bool(
            (new.get("Longitude") or "").strip()
        )
        if new_has_coords and not existing_has_coords:
            return True
        if new.get("UserNotes") and not existing.get("UserNotes"):
            return True
        return False

    def _download_index_from_url(self, url: str, source_label: str) -> Dict[str, Dict[str, Any]]:
        resp = self._session.get(url, timeout=20)
        resp.raise_for_status()

        content_type = (resp.headers.get("Content-Type") if hasattr(resp, "headers") else None)  # type: ignore[attr-defined]
        url_l = url.lower()
        looks_like_json = (
            (content_type and "application/json" in content_type.lower())
            or url_l.endswith(".json")
        )

        raw_text = resp.content.decode("utf-8", errors="replace")

        if looks_like_json:
            payload = json.loads(raw_text)
            if isinstance(payload, dict) and isinstance(payload.get("index"), dict):
                index = payload["index"]
            elif isinstance(payload, dict):
                index = payload
            else:
                raise ValueError("Backup JSON invalide (attendu dict)")

            normalized: Dict[str, Dict[str, Any]] = {}
            for k, v in index.items():
                code = str(k).strip().upper()
                if not code:
                    continue
                if isinstance(v, dict):
                    row = dict(v)
                else:
                    row = {"Code": code, "value": v}
                row.setdefault("Code", code)
                row["_source"] = source_label
                normalized[code] = row

            if not normalized:
                raise ValueError("Backup JSON vide (index vide)")

            logger.info(f"SolvedJigidi({source_label}): {len(normalized)} entrées chargées")
            return normalized

        reader = csv.DictReader(raw_text.splitlines())

        index_csv: Dict[str, Dict[str, Any]] = {}
        for row in reader:
            code = self._normalize_gc_code_key(row.get("Code"))
            if not code:
                continue
            row["_source"] = source_label
            index_csv[code] = row

        if not index_csv:
            raise ValueError("Aucune donnée chargée depuis le CSV (index vide)")

        logger.info(f"SolvedJigidi({source_label}): {len(index_csv)} entrées chargées")
        return index_csv

    def _persist_disk_csv_cache(self, index: Dict[str, Dict[str, Any]], loaded_at: datetime, source: str) -> None:
        try:
            fieldnames = [
                "Code",
                "Description",
                "Latitude",
                "Longitude",
                "Country",
                "State",
                "UserNotes",
                "Date Added",
                "_source",
                "_sheet_gid",
                "_sheet_name",
            ]

            tmp_csv = self._csv_cache_path.with_suffix(".csv.tmp")
            with tmp_csv.open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for code, row in sorted(index.items(), key=lambda kv: kv[0]):
                    out = dict(row)
                    out.setdefault("Code", code)
                    writer.writerow({k: out.get(k, "") for k in fieldnames})

            tmp_csv.replace(self._csv_cache_path)

            meta = {
                "cache_format_version": _CACHE_FORMAT_VERSION,
                "loaded_at": loaded_at.isoformat(),
                "source": source,
                "entries": len(index),
            }
            tmp_meta = self._csv_cache_meta_path.with_suffix(".json.tmp")
            tmp_meta.write_text(json.dumps(meta), encoding="utf-8")
            tmp_meta.replace(self._csv_cache_meta_path)
        except Exception as exc:
            logger.warning(f"Impossible d'écrire le cache CSV SolvedJigidi: {exc}")

    def _load_disk_csv_cache_if_available(self) -> None:
        if not self._csv_cache_path.exists() or not self._csv_cache_meta_path.exists():
            return

        try:
            meta = json.loads(self._csv_cache_meta_path.read_text(encoding="utf-8"))
            cache_version = meta.get("cache_format_version")
            if cache_version != _CACHE_FORMAT_VERSION:
                return
            loaded_at_raw = meta.get("loaded_at")
            source = meta.get("source")

            loaded_at = None
            if isinstance(loaded_at_raw, str) and loaded_at_raw:
                try:
                    loaded_at = datetime.fromisoformat(loaded_at_raw)
                except Exception:
                    loaded_at = None

            with self._csv_cache_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                index: Dict[str, Dict[str, Any]] = {}
                for row in reader:
                    code = self._normalize_gc_code_key(row.get("Code"))
                    if not code:
                        continue
                    row["_source"] = row.get("_source") or "csv_cache"
                    index[code] = row

            if not index:
                return

            with _CACHE_LOCK:
                if not _CACHE.index_by_code:
                    _CACHE.index_by_code = index
                    _CACHE.loaded_at = loaded_at
                    _CACHE.source = str(source).strip() if source else "csv_cache"

            logger.info(
                f"Cache CSV SolvedJigidi chargé ({len(index)} entrées, loaded_at={loaded_at_raw})"
            )
        except Exception as exc:
            logger.warning(f"Impossible de charger le cache CSV SolvedJigidi: {exc}")

    def _persist_snapshot(
        self,
        index: Dict[str, Dict[str, Any]],
        loaded_at: datetime,
        source: str,
    ) -> None:
        try:
            payload = {
                "cache_format_version": _CACHE_FORMAT_VERSION,
                "loaded_at": loaded_at.isoformat(),
                "source": source,
                "index": index,
            }
            tmp_path = self._snapshot_path.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(payload), encoding="utf-8")
            tmp_path.replace(self._snapshot_path)
        except Exception as exc:
            logger.warning(f"Impossible d'écrire le snapshot SolvedJigidi: {exc}")

    def _load_snapshot_if_available(self) -> None:
        if not self._snapshot_path.exists():
            return

        try:
            raw = self._snapshot_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            cache_version = payload.get("cache_format_version")
            if cache_version != _CACHE_FORMAT_VERSION:
                return
            index = payload.get("index")
            loaded_at_raw = payload.get("loaded_at")
            source = payload.get("source")

            if not isinstance(index, dict) or not index:
                return

            loaded_at = None
            if isinstance(loaded_at_raw, str) and loaded_at_raw:
                try:
                    loaded_at = datetime.fromisoformat(loaded_at_raw)
                except Exception:
                    loaded_at = None

            with _CACHE_LOCK:
                if not _CACHE.index_by_code:
                    _CACHE.index_by_code = index
                    _CACHE.loaded_at = loaded_at
                    _CACHE.source = str(source).strip() if source else "snapshot"

            if not self._csv_cache_path.exists() and loaded_at is not None:
                self._persist_disk_csv_cache(
                    index,
                    loaded_at=loaded_at,
                    source=str(source).strip() if source else "snapshot",
                )

            logger.info(
                f"Snapshot SolvedJigidi chargé ({len(index)} entrées, loaded_at={loaded_at_raw})"
            )
        except Exception as exc:
            logger.warning(f"Impossible de charger le snapshot SolvedJigidi: {exc}")

    def _build_plugin_info(self, start_time: float, cache_status: str) -> Dict[str, Any]:
        execution_time = (time.time() - start_time) * 1000
        return {
            "name": self.name,
            "version": self.version,
            "execution_time_ms": round(execution_time, 2),
            "cache_status": cache_status,
        }

    def _error(self, start_time: float, message: str, details: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "status": "error",
            "summary": message,
            "results": [],
            "plugin_info": self._build_plugin_info(start_time, cache_status="error"),
        }
        if details:
            payload["error"] = details
        return payload


plugin = SolvedJigidiPlugin()


def execute(inputs: Dict[str, Any]) -> Dict[str, Any]:
    return plugin.execute(inputs)
