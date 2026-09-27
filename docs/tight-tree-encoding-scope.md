# Scope: tight `(indent, qname, ...)` rows for domain-member trees and calculation

Status: **scoping only — nothing here is implemented.** Queue item M10.

Goal: move mireport's baked-JSON encoding of domain-member trees (`explicitDimensionDomains`,
`ee20Domain`) and calculation (`calculation`) off today's per-arc dicts / flat edge list onto the
same compact indented-rows shape that presentation already uses (`PresentationRow`), with a true
calculation cycle treated as a hard error rather than accommodated.

## 1. Real-data cycle check

**Verdict: the premise holds. No summation-item cycle occurs in any real or test taxonomy on this
machine.** A calculation cycle is spec-legal (XBRL 2.1 permits one) but, as far as all available
evidence goes, purely theoretical, so per the maintainer's decision it becomes a hard error rather
than something the encoding accommodates.

How it was checked (throwaway scripts in the agent scratchpad, not committed):

- **DTS-level, via Arelle** (`arelle-release` from `transition_plans/.venv`, offline, package
  loaded from the zip). For each entry point, every ELR's summation-item relationship set was built
  exactly as mireport builds it: 2003 and 2023 arcroles combined into one set per ELR
  (`XbrlConst.summationItems`). A DFS three-colour cycle search ran over each ELR's edges. The
  script also *simulated the proposed tight walk* (§2.2): walk from `rootConcepts`, expanding each
  concept at most once per ELR. It then checked that the walk emitted every arc in the set. It
  would fall short only if part of the network were unreachable from a root, which in a finite
  graph means a cycle.
- **Baked JSON**: the same cycle search over the `calculation` section of all four
  `src/mireport/data/taxonomies/vsme-*.json`.
- **Supplementary raw-XML sweep**: every calculation linkbase on the machine (loose files and
  inside zips), one ELR per `calculationLink`, prohibited arcs dropped. This does not merge base
  sets across files, which is why the DTS-level check above is the authoritative one. 144
  linkbases, about 27.9k arcs (duplicates included), **0 cyclic ELRs**. It covered the four VSME
  source zips in `webapp_taxonomies/`, both IFRSAT zips, the L'Oréal ESEF filer extension in
  `oim/.../examples/loreal-2025-12-31-ReportPkg.zip` (the only non-standard-setter, real filer
  calc on disk), the `xbrl21_to_tavi` calculation fixtures and all generated forward output under
  `.claude/test-scratch/`.
- **Negative control**: the detector finds `A→B→C→A` and `A→A`, and finds nothing in a diamond DAG.

| DTS (entry point) | calc ELRs | arcs | arcrole | multi-root ELRs | concepts both total and item | items with >1 parent (same ELR) | of which are totals (shared subtrees) | cyclic ELRs | tight walk emitted every arc |
|---|---|---|---|---|---|---|---|---|---|
| VSME 2024-12-17 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| VSME 2025-07-30 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| VSME 2026-02-01 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| VSME 2026-05-01 (baked) | 7 | 31 | 2023 | 4 | 2 | 4 | 0 | **0** | yes |
| IFRSAT 2024 Full IFRS + MC | 46 | 1312 | 2023 | 24 | 159 | 2 | 0 | **0** | yes |
| IFRSAT 2024 Combined | 67 | 1786 | 2023 | 33 | 198 | 2 | 0 | **0** | yes |
| IFRSAT 2024 IFRS for SMEs | 21 | 474 | 2023 | 9 | 39 | 0 | 0 | **0** | yes |
| IFRSAT 2025 Full IFRS | 46 | 1312 | 2023 | 24 | 159 | 2 | 0 | **0** | yes |
| IFRSAT 2025 Early IFRS 18 | 45 | 1289 | 2023 | 23 | 157 | 2 | 0 | **0** | yes |

(IFRSAT 2024 Deprecated and 2025 Management Commentary carry no calculation arcs. The identical
2024/2025 Full IFRS calc counts are real: the forward output of both editions under
`.claude/test-scratch/` agrees, 46/1312.)

What the numbers mean for the design:

- **Multiple roots are routine**: about half of all real calc ELRs have more than one. As already
  settled, this is not an obstacle: `extractPresentation` emits one `(0, root)` row per root.
- **A concept that is both total and item is routine** (159 in IFRS Full). Also not an obstacle:
  the subtotal appears at depth *n* under its parent, with its own items at *n+1*.
- **A shared calc subtree (a total reached from two parents in one ELR) never occurs in real
  data** (0 everywhere). A shared *leaf* item does (2–4 per DTS). The encoding still defines what
  happens for a shared total (§2.2), because it is legal and cheap to specify, but no real data
  exercises it.

### 1.1 Domain-member networks (for §2.1)

XDT already forbids directed cycles here. These numbers are about the two properties the tight
encoding must preserve:

| DTS | domain-member arcs | with `xbrldt:targetRole` | dimension-domain arcs with `targetRole` | members with >1 parent (same ELR) | `usable="false"` arcs |
|---|---|---|---|---|---|
| IFRSAT 2024 Full IFRS + MC | 3207 | 0 | 0 | 9 | 0 |
| IFRSAT 2024 Combined | 3631 | 0 | 0 | 9 | 0 |
| IFRSAT 2025 Full IFRS | 3339 | 0 | 0 | 13 | 0 |
| IFRSAT 2025 Early IFRS 18 | 3375 | 0 | 0 | 14 | 0 |

Baked VSME trees (`explicitDimensionDomains` + `ee20Domain`): 2808–3024 arcs per edition, no
multi-parent members, no unusable arcs. There is **exactly one real ELR crossing**, in VSME
2025-07-30 and later: `vsme:WasteGeneratedTable` / `vsme:TypeOfWasteAxis`. Its dimension-domain
arc sits in `role-119` and carries a `targetRole` to the `.../domain` ELR, so all 973 member arcs
of that tree record `elr = .../domain` while the tree's own `"elr"` is `role-119`. The crossing is
on the **dimension-domain arc, not partway down the member tree**. Nothing on the machine crosses
ELRs *within* a domain-member walk. That shapes §2.1: a tree-level field carries the real case,
and a per-row override covers the XDT-legal but unobserved mid-tree case.

So both preservation constraints are real in production data, not theoretical: the ELR change
(VSME) and DAG de-duplication (IFRSAT, 9–14 multi-parent members per DTS). `usable="false"` has no
real-data hits, which U1 had already noted; it is covered by the `xbrl21_to_tavi` hand-built
`usable` fixture.

## 2. Proposed encoding

_Still investigating._

## 3. Consumer inventory

_Still investigating._

## 4. Re-bake and regeneration consequences

_Still investigating._

## 5. Recommended sequencing

_Still investigating._
