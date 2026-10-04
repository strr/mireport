# Typed fact model, layout tidy-up, and prior-period support

Branch `strr/typed-model-prior-period`, on top of `strr/xbrljson-ixbrl`. Refactor first, features after,
and every refactor step must leave the rendered output **byte-identical** (see "How each step is proved").

## Why

1. **The internal fact representation is aoix's syntax.** `Fact._aspects` is a `dict[str | QName, str | QName]`
   whose string values are pre-quoted for aoix (`'"4"'`, `'"a/b"'`, `'"<w>x</w>"'`), with typed dimensions held under
   a `"typed <qname>"` key. Four places re-parse those strings (`tidyTdValue`, `_aoixTypedDimension`,
   `Fact.unitSymbol`, layout's `_dimensionValues`/`_assemble_typed_dim`), and `Fact.__eq__` hashes the strings.
   aoix has deprecated the `typed` keyword and `complex-units`; we only avoid them at the last moment, and keep the
   deprecated `typed` form as a fallback.
2. **`layout.py` (762 lines) has three near-identical grid assemblers plus a fourth for the dimensional fallback**,
   with different duplicate policies (keep last/debug versus keep first/warn), a latent `KeyError`, groups with 0 or
   2+ dimensions silently yielding no table, a template macro that does not exist (`render_period`), and header
   derivation (`_build_header_rows`) with no direct tests.
3. **Nothing in layout knows about a second reporting period.** No cell key includes the period, so a prior-period
   fact collides with the current one and one silently wins. The title, cover, document information and the
   "all disclosures relate to the period above" note all assume a single period.

## What the exploration found that shapes the plan

* The **VSME Excel template is single-period** (FAQ item 7: "one reporting period only ... EFRAG is evaluating
  multiperiod reporting"). There are no comparative columns to read. The only non-default periods are the C3
  Baseline/Target years, which are *also* distinguished by a `ReportingScopesAxis` member and are not "prior" data.
  So "prior period in every mode" is built into the **report model and every layout mode**, and exercised through the
  **xBRL-JSON reader** (already multi-period) and synthetic data. Reading prior data *from Excel* needs a template
  contract EFRAG has not defined; this plan lays the groundwork (period roles in config, fix the
  "last configured period becomes the default" bug) and stops there.
* aoix already declares every named period and selects one per fact; an instant is derived from the concept's period
  type plus the named period's end. A prior-period instant is therefore the prior duration's end: no instant holder
  is needed in the report.
* The characterization snapshots pin the string forms (`aspects` as strings), so they are regenerated deliberately,
  with the **rendered HTML as the real invariant**.

## Design

### A. Typed model (`mireport/report/model.py`, new)

| Type | Replaces |
|---|---|
| `Unit` (frozen: `numerator: tuple[QName, ...]`, `denominator: tuple[QName, ...] = ()`) with `Unit.simple()`, `Unit.parse()`, `is_currency`, `str()` in OIM unit syntax (`a`, `a/b`, `(a*b)/c`) | `units` / `monetary-units` / `complex-units` aspects and the `rpartition("/")` parsing in `unitSymbol` |
| `ExplicitDimensionValue(dimension: Concept, member: Concept)` | QName-keyed aspects |
| `TypedDimensionValue(dimension: Concept, value: str)` (the typed member's text, unescaped; wrapper derived from `dimension.typedElement`) | `"typed <q>"` keys, `tidyTdValue`, `_TYPED_XML_RE` |
| `ReportPeriod(name: str, period: DurationPeriodHolder, role: PeriodRole)` | period *name strings* and `report._periods` access from `Fact` |
| `Fact` fields: `concept, value, period, unit, decimals: int \| "INF" \| None, scale: int \| None, explicit_dimensions, typed_dimensions, hidden_value: tuple[Concept, ...] \| None` | `_aspects` |
| `ReportDefaults` (entity identifier + scheme, currency, number format) | `_defaultAspects` and `setDefaultAspect(str, str)` |

`period-type`, `escape`, `transform` and `fn-refs` are **derived at emission** (from the concept, value and
footnotes), not stored. They were never part of a fact's identity.

aoix text is produced in one module, `mireport/report/aoix.py`, from the typed fields, new-style only
(`typed-value-wrapper=`, `units=a/b`). The deprecated forms stop existing anywhere in the code.

`FactBuilder`'s public setters keep their signatures so the Excel reader and xBRL-JSON reader barely change; it gains
`setUnit(Unit)`. `Fact` keeps `value` and `footnotes` mutable (partial facts, footnotes) and is hashed on
`(concept, value, period, unit, decimals, scale, dimensions)`.

**Decisions taken (flagged so they can be reversed):**
* A typed value containing a double quote **is rejected** with a clear `InlineReportException`, because aoix's quoted
  strings cannot express it without the deprecated keyword. (Today it silently uses the deprecated form.) The Excel
  path already turns that exception into a "fact not added" warning.
* `Unit` allows more than one measure per side (aoix writes `units=u1/(u2*u3)`); the old one-per-side limit was a
  limit of the string representation. `unitSymbol` for those joins symbols.
* Monetary facts get their currency stamped from the report default at build time, instead of relying on aoix's
  default. The rendered iXBRL is the same (aoix resolves units by value).

### B. Layout (`mireport/report/layout/`, package)

* `model.py` sections, `Table`, heading cells. `organiser.py` the pipeline. `pivot.py` one generic pivot builder.
  `headers.py` header/unit/period derivation as pure, tested functions. `strategies.py` (today's `disclosure_layout.py`
  stays where it is).
* **One pivot builder** replaces `_assemble_explicit_dim_as_columns/_rows`, `_assemble_typed_dim` and
  `_assemble_from_dimension_values`. It takes a row axis and a column axis, each either *concepts* or a *dimension
  key* (any number of explicit and typed dimensions, in a stated order), and fills a grid in one pass.
  The existing orientation and ordering rules are kept as policies (explicit: domain members in presentation order,
  domain as columns when it is no wider than the concepts; typed: numeric-aware sort, always rows; fallback: smaller
  axis as columns), so VSME's output does not move.
* Fixes that fall out: one duplicate policy (keep first, warn; strict mode already makes an unrendered fact an
  error), no `KeyError` on a fact without the typed dimension, groups with 0 or 2+ dimensions get a table instead of
  none, the dead `TableStyle.NoTaxonomyDefinedDimensions` goes, and the broken `render_period` macro call is fixed.
* The `_format_numeric_value` quirk (`decimals == 0` is falsy, so it displays with infinite precision) is **left
  alone in the refactor** and examined in its own commit afterwards, because fixing it can change output.

### C. Prior period

* `PeriodRole`: `CURRENT` (exactly one; the default), `PRIOR` (at most one), `OTHER` (the C3 baseline/target years).
  `InlineReport` gains `setCurrentPeriod(...)`/`addPriorPeriod(...)`; `addDurationPeriod` and `setDefaultPeriodName`
  stay and mean `OTHER`/`CURRENT`.
* **The period joins the cell key.** A column key is `(period, column-dimension-key)`.
  * If every column has exactly one period (today's baseline/target table, or a table of only current facts), the
    period stays a per-column header row or is hoisted, exactly as now.
  * If a column would hold more than one period (current *and* prior), the **period becomes an outer column group**
    (`Current | Prior`, each spanning the sub-columns). Units are then per sub-column as today.
* **List sections** show each concept once with a value column per period present (`label | current | prior`), `—`
  where one is missing. A section with only current facts renders exactly as now.
* Title, cover, document information and the "all disclosures are related to the reporting period above" note state
  the current period, and add "Prior period" when there is one. The output file name keeps using the current year.
* **xBRL-JSON reader:** the current period is the *latest-ending* duration (not the most frequent, as now), the prior
  is the duration ending a year earlier (same length), anything else is an error with a clear message. Names become
  stable (`cur`, `prior`) instead of `period1..N`.
* **Excel config:** `periods[].role`, and a prior period whose named ranges are absent is skipped rather than an
  error. The "last entry wins the default" bug is fixed. **Not done:** reading prior *values* from Excel; that needs
  a template contract from EFRAG.

## Order of work (each step is a commit that leaves `pytest` and the golden check green)

1. **Typed model** (pure): `model.py` + tests.
2. **aoix emitter**: `aoix.py` from typed fields + tests, including the rejected-quote case.
3. **Fact / FactBuilder / InlineReport onto typed fields**, every consumer updated, snapshots regenerated.
   Golden HTML identical.
4. **Layout: move into a package**, no behaviour change.
5. **Layout: the generic pivot**, assemblers removed, bug fixes. Golden identical.
6. **Layout: headers/units/periods as pure functions**, with tests (there are none today).
7. Examine the `decimals == 0` formatting quirk on its own, with evidence either way.
8. **Prior period**: period roles in the report; period in the cell key; outer period group; list columns;
   title/cover/doc-info; xBRL-JSON reader roles; Excel config groundwork.
9. **Tests for prior period in every mode**, including every VSME layout mode via a synthetic prior period (clone the
   sample's facts into a prior period) and a TPT prior-period sample.
10. Docs (`CLAUDE.md`, this file's "what changed"), and the TPT workspace's gate picks up a prior-period sample.

## How each step is proved

* **Golden render**: 22 files (the 6 test workbooks + the 1.3.0 sample + all 15 TPT reports) rendered with a pinned
  clock (`SOURCE_DATE_EPOCH`) before the work started, determinism confirmed by rendering twice. After every
  refactor commit the same 22 are re-rendered and must be byte-identical (generator version string normalised).
* `pytest` (unit, integration, `--run-slow`), ruff, and the workspace's `verify-all.sh` step 9 (Arelle validation and
  fact-for-fact equality with the source JSON) at the end of each phase.
* New behaviour (phase C) gets its own golden set, reviewed once, then pinned.

## Out of scope / needs a decision from others

* Reading prior-period values from the VSME Excel template (EFRAG template contract).
* Instants as first-class report periods (aoix derives them; revisit only if a prior *instant-only* report appears).
* `inline-report-flat-list.html.jinja` looks unused; it is left alone and noted.
