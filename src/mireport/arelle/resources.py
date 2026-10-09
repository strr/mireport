"""The resources Arelle ships, which a report needs no taxonomy package for.

Arelle bundles the XBRL International core schemas (and a few others) in a
cache keyed by scheme, host and path. A reference is answered from it only when
it is spelt as the cache is, with one exception that Arelle itself makes: the
four spellings of xbrl.org (http or https, with or without www) are one location.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

from arelle.WebCache import XBRL_ORG_CACHE_REDIRECTS

_SCHEMES = frozenset({"http", "https"})


class BundledResources:
    def __init__(self, cacheDirectory: Path | None = None) -> None:
        """`cacheDirectory` defaults to the cache that Arelle itself ships."""
        if cacheDirectory is None:
            cacheDirectory = Path(str(files("arelle") / "resources" / "cache"))
        self._cacheDirectory = cacheDirectory

    def contains(self, url: str) -> bool:
        """Whether Arelle holds the file at `url`, spelt exactly so."""
        url = urldefrag(url).url
        spellings = [url]
        for prefix, others in XBRL_ORG_CACHE_REDIRECTS.items():
            if url.startswith(prefix):
                spellings += [other + url[len(prefix) :] for other in sorted(others)]
        return any(self._cached(spelling) for spelling in spellings)

    def _cached(self, url: str) -> bool:
        parts = urlsplit(url)
        if parts.scheme not in _SCHEMES or not parts.netloc:
            return False
        relative = Path(parts.scheme) / parts.netloc / parts.path.lstrip("/")
        return (self._cacheDirectory / relative).is_file()
