# PDF/UA-1, Matterhorn Protocol 1.1, and WCAG 2.1 AA Check Reference

## Contents

- [Overview](#overview)
- [Catalog and Metadata Checks](#catalog-and-metadata-checks)
- [Tag Tree and MCID Integrity Checks](#tag-tree-and-mcid-integrity-checks)
- [Heading, Figure, and Table Semantics](#heading-figure-and-table-semantics)
- [Interactive AcroForm and Tab-Order Checks](#interactive-acroform-and-tab-order-checks)
- [Severity Classification and Pass Score Weights](#severity-classification-and-pass-score-weights)
- [Human Verification Boundary](#human-verification-boundary)

## Overview

This reference maps the automated checks in `scripts/pdf_ua_inspect.py` to ISO 14289-1 (PDF/UA-1), Matterhorn Protocol 1.1 failure conditions, WCAG 2.1 Level AA success criteria, and Revised Section 508 (E205.4). Use it when explaining audit findings or writing remediation guidance in `pdf_ua_remediation_plan.md`.

## Catalog and Metadata Checks

- **Tagged PDF (`is_tagged` / `/StructTreeRoot` and `/MarkInfo/Marked`)**:

  - PDF/UA-1 Clause 7.1 / Matterhorn 01-005 & 01-006: Document must contain a valid `/StructTreeRoot` and `/MarkInfo` with `/Marked true`.
  - WCAG 2.1 AA 1.3.1 (Info and Relationships): Without a tag tree, assistive technology cannot determine reading order, headings, lists, or table relationships.
  - Print-to-PDF workflows strip `/StructTreeRoot`, triggering `CRITICAL_UNTAGGED_PDF`.

- **Document Language (`/Catalog/Lang`)**:

  - PDF/UA-1 Clause 7.2 / Matterhorn 11-001: Natural language must be declared using a valid BCP-47 tag (for example `en-US`, `es-MX`, `fr-CA`).
  - WCAG 2.1 AA 3.1.1 (Language of Page): Screen readers rely on `/Lang` to load the correct pronunciation dictionary.

- **Document Title (`dc:title` in XMP and `/Info/Title`)**:

  - PDF/UA-1 Clause 7.1 / Matterhorn 06-001 & 06-002: XMP metadata must contain a non-empty `dc:title` matching the human-readable document title.
  - WCAG 2.1 AA 2.4.2 (Page Titled): Title must describe the document topic or purpose, not a raw filename like `Final_v3_PRINT.pdf`.

- **Display Document Title (`/ViewerPreferences/DisplayDocTitle`)**:

  - PDF/UA-1 Clause 7.1 / Matterhorn 07-001: `/ViewerPreferences` must set `/DisplayDocTitle true` so PDF viewers show the document title instead of the filename in the window title bar.

## Tag Tree and MCID Integrity Checks

- **Duplicate MCIDs per Page (`duplicate_mcids`)**:

  - PDF/UA-1 Clause 7.1 / Matterhorn 01-003 & 01-004: Every Marked Content Identifier (`/MCID`) on a given page must map uniquely within the structure tree.
  - Operational impact: Duplicate MCIDs on the same page are a primary root cause of PAC 2026 crashes and freezes ("MCID already present"), commonly introduced by copy-pasting frames in InDesign, merging PDFs in Acrobat, or corrupt incremental saves.
  - Severity: **Critical** (blocks automated validation and corrupts screen-reader virtual buffers).

- **Scanned Image-Only PDF (`SCANNED_IMAGE_PDF`)**:

  - Triggered when a PDF has zero extractable text operators across pages and no `/StructTreeRoot`.
  - WCAG 2.1 AA 1.4.5 (Images of Text) and 1.1.1 (Non-text Content): Requires OCR and source-document reconstruction before tagging.

## Heading, Figure, and Table Semantics

- **Heading Level Hierarchy (`heading_skips`)**:

  - PDF/UA-1 Clause 7.4.2 / Matterhorn 14-002 & 14-003: Numbered headings (`H1` through `H6`) must not skip levels (for example `H1` directly to `H3`, or `H2` directly to `H4`).
  - WCAG 2.1 AA 1.3.1 (Info and Relationships) and 2.4.6 (Headings and Labels): Screen-reader users navigate long reports by heading outline; skipped levels hide document hierarchy.
  - Severity: **High**.

- **Figure Alternative Text (`missing_alt_figures`)**:

  - PDF/UA-1 Clause 7.3 / Matterhorn 13-004: Every `<Figure>` structure element that conveys meaning must include a non-empty `/Alt` attribute (decorative graphics should be marked as `/Artifact` rather than `<Figure>`).
  - WCAG 2.1 AA 1.1.1 (Non-text Content): Missing `/Alt` causes screen readers to skip the image or announce raw path metadata.
  - Severity: **High**. Complex charts, infographics, and medical or engineering diagrams require human domain-expert review rather than automated caption guessing.

- **Table Header Cells (`table_header_issues`)**:

  - PDF/UA-1 Clause 7.5 / Matterhorn 15-001 & 15-003: Data `<Table>` elements must include `<TH>` header cells (with `/Scope` set to `Column` or `Row` in Acrobat/source) so assistive technology can announce column and row headers for each `<TD>` data cell.
  - WCAG 2.1 AA 1.3.1 (Info and Relationships): Tables without `<TH>` cells become unnavigable grids of unlabelled numbers.
  - Severity: **High**.

## Interactive AcroForm and Tab-Order Checks

- **Page `/Tabs` Key**:

  - PDF/UA-1 Clause 7.18 / Matterhorn 28-010: Pages containing interactive form `/Widget` annotations must set `/Tabs /S` (Use Document Structure) so keyboard focus follows logical reading order.

- **Visual Reading Order vs. `/Annots` Order (`form_tab_order_mismatches`)**:

  - WCAG 2.1 AA 2.4.3 (Focus Order): Keyboard Tab order must follow the visual flow of the form.
  - `scripts/pdf_ua_inspect.py` sorts `/Widget` bounding boxes `[x0, y0, x1, y1]` top-to-bottom (10pt Y-tolerance) and left-to-right for `--layout rows` (or column-first for `--layout columns`) and compares against the page `/Annots` sequence.
  - Severity: **High**.

- **Accessible Form Field Tooltips (`missing_form_tooltips`)**:

  - PDF/UA-1 Clause 7.18.1 / Matterhorn 28-002 & WCAG 2.1 AA 4.1.2 (Name, Role, Value): Every interactive `/Widget` field must expose a human-readable `/TU` (User Tooltip) describing the expected input.
  - Severity: **Medium** (or **High** when the field has no visible programmatic label).

## Severity Classification and Pass Score Weights

`scripts/pdf_ua_inspect.py` computes `pass_score_pct` (0.0 to 100.0) deterministically across seven check categories:

- Tagging present (`/StructTreeRoot` + `/MarkInfo/Marked`): 25 points.
- Core metadata (`/Lang`, `/Title` + XMP `dc:title`, `/DisplayDocTitle`): 15 points (5 points each).
- Zero duplicate MCIDs per page: 15 points.
- Clean heading hierarchy (zero skips): 10 points.
- All `<Figure>` tags have `/Alt` text: 15 points.
- All `<Table>` tags have `<TH>` header cells: 10 points.
- Clean AcroForm tab order (`/Tabs /S`, visual `/Annots` order, and `/TU` tooltips): 10 points (full credit when no form widgets exist).

## Human Verification Boundary

Automated static PDF inspection cannot verify whether `/Alt` text is semantically accurate, whether reading order makes sense in multi-column layouts with callout boxes, whether color contrast meets 4.5:1, or whether assistive technology announces dynamic widgets cleanly. Final legal compliance under PDF/UA-1, WCAG 2.1 AA, and Section 508 always requires manual review plus live testing with NVDA or JAWS.
