from __future__ import annotations

import logging
from functools import cache
from gettext import NullTranslations
from importlib.resources import files
from io import BytesIO

from babel import Locale
from babel.messages.mofile import write_mo
from babel.messages.pofile import read_po
from babel.support import Translations

from mireport.entrypoints import EntryPointSet, entryPointSetOf
from mireport.json import getJsonFiles, getObject
from mireport.localise import as_xmllang, split_base_territory

L = logging.getLogger(__name__)


def _loadConfigs() -> tuple[
    dict[str, dict],
    dict[EntryPointSet, dict],
    dict[EntryPointSet, str],
    dict[EntryPointSet, str],
]:
    """Configs by file name and by entry-point set, the per-entry-point
    layout strategy overrides and the per-entry-point template version
    settings; each URL a config names is a one-document set. Parsed once,
    here, so lookups never re-normalise config URLs."""
    byFilename: dict[str, dict] = {}
    byEntryPoint: dict[EntryPointSet, dict] = {}
    layoutOverrides: dict[EntryPointSet, str] = {}
    templateVersionSettings: dict[EntryPointSet, str] = {}
    for resource in getJsonFiles(__name__):
        config = byFilename[resource.name] = getObject(resource)
        for url, strategyName in (
            config.get("layoutStrategy", {}).get("entryPoints", {}).items()
        ):
            layoutOverrides[entryPointSetOf(url)] = strategyName
        for url, setting in config.get("templateVersions", {}).items():
            templateVersionSettings[entryPointSetOf(url)] = setting
        entryPoints = config.get("taxonomyEntryPoints", {})
        for ep in (
            entryPoints.get("supportedEntryPoint"),
            *entryPoints.get("oldEntryPoints", []),
        ):
            if not ep:
                continue
            if (entryPointSet := entryPointSetOf(ep)) in byEntryPoint:
                L.warning(
                    "Entry point %s already registered; ignoring duplicate in %s",
                    ep,
                    resource.name,
                )
            else:
                byEntryPoint[entryPointSet] = config
    return byFilename, byEntryPoint, layoutOverrides, templateVersionSettings


(
    _CONFIG_BY_FILENAME,
    _CONFIG_BY_ENTRY_POINT,
    _LAYOUT_OVERRIDES,
    _TEMPLATE_VERSION_SETTINGS,
) = _loadConfigs()


def getDisclosureConfig(entryPointSet: EntryPointSet) -> dict | None:
    """The config for exactly this entry-point set, if any."""
    return _CONFIG_BY_ENTRY_POINT.get(entryPointSet)


def getTemplateVersionSetting(entryPointSet: EntryPointSet) -> str | None:
    """How the latest Digital Template version for this entry-point set is
    determined: a version number, ``"auto"`` (ask the migration tool) or
    ``"none"`` (no migration); None when the config says nothing."""
    return _TEMPLATE_VERSION_SETTINGS.get(entryPointSet)


def getLayoutStrategyName(entryPointSet: EntryPointSet) -> str | None:
    """The layout strategy named for this entry-point set: its override, else
    its config's default; None when no config covers it."""
    if (config := _CONFIG_BY_ENTRY_POINT.get(entryPointSet)) is None:
        return None
    return _LAYOUT_OVERRIDES.get(
        entryPointSet, config.get("layoutStrategy", {}).get("default")
    )


def getReportTranslations(
    entryPointSet: EntryPointSet, locale: Locale
) -> NullTranslations:
    """The catalog of the report's fixed strings that this entry-point set's config names,
    for the locale's full tag or else its base language. NullTranslations (the English
    msgids) when there is neither."""
    if (config := _CONFIG_BY_ENTRY_POINT.get(entryPointSet)) and (
        directory := config.get("reportTranslations")
    ):
        tag = as_xmllang(locale)
        for language in dict.fromkeys((tag, split_base_territory(tag)[0])):
            if (compiled := _compiledCatalog(directory, language)) is not None:
                return Translations(BytesIO(compiled))
    return NullTranslations()


@cache
def _compiledCatalog(directory: str, language: str) -> bytes | None:
    """``<directory>/<language>.po`` compiled to .mo bytes (fuzzy entries left out), or None
    when there is no such catalog."""
    po = files(__name__).joinpath(*directory.split("/"), f"{language}.po")
    if not po.is_file():
        return None
    with po.open("rb") as f:
        catalog = read_po(f)
    mo = BytesIO()
    write_mo(mo, catalog)
    return mo.getvalue()


# Back-compat: existing callers (e.g. processor.py) import this by name
VSME_DEFAULTS: dict = _CONFIG_BY_FILENAME["vsme.json"]
