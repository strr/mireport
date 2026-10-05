# Next phase (design only, not built): Report split, merging a prior workbook, xBRL-JSON/CSV output

Status: designed 2026-10-05, **not started**; build only on Stuart's go-ahead. Context: branch
`strr/unit-strings-duplicates` (unit strings, validation on `addFact`, duplicates, structure check, xBRL-JSON
snapshots) is done and sits on `strr/typed-model-prior-period`.

## 1. Split `Report` (data) from `InlineReport` (rendering)

`InlineReport` mixes three things: report data (taxonomy, entity, currency, periods and roles, facts and indexes,
footnotes, partial facts), presentation settings (title, theme, locale, overrides) and rendering (aoix defaults,
periods, namespaces, template, packaging). `Fact` also reaches back into the report (`_outputLocale`, `taxonomy.UTR`)
for formatting.

* `Report`: the data. `InlineReport(Report)` keeps title/theme/locale/overrides and the aoix/template/packaging code,
  so readers and the webapp are unchanged at first.
* Then move `Fact.html_format_value` / `unitSymbol` (locale, UTR) into the renderer so `Fact` is a pure value type.
* Why now: a second output (xBRL-JSON, xBRL-CSV) needs only data, and today needs an `InlineReport`.

## 2. Merge last year's workbook as the prior period

`Report.mergePrior(other)`:

* Same entity identifier and scheme, else an error. The other report's current period must be about a year before
  ours (reuse `_a_year_before`).
* **Re-base, do not merge by QName.** Each VSME taxonomy version has its own namespace
  (`.../vsme/2025-07-30/vsme` vs `.../2026-05-01/vsme`). Measured: 362 of 374 and 374 of 401 concept names carry over
  between neighbouring versions, 401 of 401 between the two newest. Facts are re-based by `prefix:localName`
  (concept, dimensions, members, enumeration members) through `FactBuilder`, which re-validates them against this
  year's hypercubes, units and datatypes (`Taxonomy.getConcept("vsme:X")` already resolves through the new prefix).
* Unmappable facts are dropped with counted messages. Baseline/target periods and footnotes are skipped. The period
  becomes our prior. Run `InlineReport.duplicates()` afterwards.
* Surfaces: CLI `--prior-xlsx`, then the web upload. Needs an EFRAG contract only for reading prior values *inside*
  one template; merging two workbooks needs none.

## 3. Outputs

* **xBRL-JSON writer** from `Report`: `Unit.toUnitString`, ISO periods, the XBRL value (apply `scale`), footnotes as
  note facts. A data-only prototype already exists in `tests/unitTests/xbrl_json_snapshot.py` (promote it). Extension
  properties (`mireport:` prefix, `urn:mireport:xbrl-json-extension`) carry what the OIM has no place for. Round-trip
  against Arelle's JSON from our own iXBRL, as the TPT gate does.
* **xBRL-CSV later**, reusing `GridBuilder` to group facts per hypercube.

## Other open items (not part of this design)

* aoix cannot quote a typed value containing `"`: we refuse it. Fix upstream (`ixbrltemplates/reparser.py`, its
  quoted-string regex has `\.` where an escape `\\.` looks intended; inference, not documented), separate repo.
* A typed dimension's value *content* is not validated (only presence).
* A concept in several presentation groups repeats its facts in each group's table (existing design).
* Output has never been looked at in a browser (human only).
