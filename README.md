# PDF/UA Preflight Remediator

Local pure-Python PDF accessibility inspector and non-destructive patcher for **PDF/UA-1 (ISO 14289-1), WCAG 2.1 AA, and Section 508**. Detects missing `/StructTreeRoot` tag trees, per-page duplicate `/MCID` entries (the primary cause of PAC 2026 freezes), missing `/Lang` and XMP `dc:title` metadata, skipped heading levels, `<Figure>` tags without `/Alt` text, `<Table>` tags missing `<TH>` headers, and out-of-order AcroForm tab sequences.

Built for **accessibility engineers, government/higher-ed publishing teams, compliance specialists, and document authors** remediating Word, InDesign, and Acrobat PDFs.

## Quick Install

```bash
npx skills add ameer-agent-dev/pdf-ua-preflight-remediator --skill pdf-ua-preflight-remediator
```

*(Single-repo shorthand: `npx skills add ameer-agent-dev/pdf-ua-preflight-remediator`)*

## Required & Optional Inputs

- **Mandatory**: Local path to target `.pdf` file.
- **Optional**:
  - Target standard: `PDF/UA-1` (default), `WCAG 2.1 AA`, or `Section 508`.
  - Execution mode: `audit-only` (default) or `audit-and-safe-patch` (writes `<stem>_patched.pdf`).
  - Document language: BCP-47 code such as `en-US` (prompted once in patch mode if `/Lang` is missing).
  - Form tab-order geometry: `rows` (default, 10pt Y-tolerance) or `columns` (multi-column forms).

## Example Prompts

- **Normal run (pre-flight + non-destructive safe patch)**:
  > "Run a PDF/UA-1 preflight audit and safe patch on `./intake_application_form.pdf` using language `en-US` and title `2026 Resident Intake Form`, then generate the page-by-page remediation blueprint."
- **Edge case (PAC 2026 crash / scanned or untagged PDF)**:
  > "PAC 2026 freezes halfway through `./annual_report.pdf` with an MCID error. Inspect the PDF tag tree locally in `audit-only` mode, pinpoint exact pages and object IDs with duplicate MCIDs or untagged sections, and give me InDesign/Acrobat fix steps."

## Expected Output Files

- `pdf_ua_audit.json`: Deterministic structural audit containing `is_tagged`, `banners` (`SCANNED_IMAGE_PDF`, `CRITICAL_UNTAGGED_PDF`), `metadata_checks`, `duplicate_mcids`, `heading_skips`, `missing_alt_figures`, `table_header_issues`, `form_tab_order_mismatches`, and `pass_score_pct`.
- `pdf_ua_remediation_plan.md`: Prioritized Critical/High/Medium blueprint with page numbers, PDF object IDs (`12 0 R`), source-authoring fixes (Word/InDesign vs. Acrobat), and screen-reader sign-off checklist.
- `<stem>_patched.pdf` *(only in `audit-and-safe-patch` mode)*: Non-destructive patched copy with `/Catalog/Lang`, `/Info/Title` + XMP `dc:title`, `DisplayDocTitle true`, `/Tabs /S`, and visually re-sorted AcroForm `/Annots` while leaving the source PDF untouched.

## Bundled Scripts, References & Assets

| Path | Purpose |
| :--- | :--- |
| `SKILL.md` | Pre-flight workflow, encrypted/scanned gates, and safe-patch verification |
| `scripts/pdf_ua_inspect.py` | Inspects catalog metadata, tag tree, MCIDs, headings, figures, tables, and widget bounding boxes (`--help` supported) |
| `scripts/pdf_ua_safe_patch.py` | Writes `<stem>_patched.pdf` with SHA-256 overwrite protection, XMP metadata, and visual widget sorting (`--help` supported) |
| `references/pdf_ua_matterhorn_checks.md` | Mapping between PDF object defects, Matterhorn Protocol 1.1, WCAG 2.1 AA, and Section 508 |
| `references/indesign_word_export_fixes.md` | Practical remediation recipes for Word, InDesign, and Adobe Acrobat Pro |
| `assets/remediation_report_template.md` | Customizable Markdown template for `pdf_ua_remediation_plan.md` |

### Python Dependencies

Requires Python 3 and pure-Python `pypdf` (no C extensions or Acrobat licenses needed):

```bash
pip install pypdf
```

## Autonomy & Safety Boundary

- **Autonomous local operations**: Inspects PDF objects locally offline as untrusted data and, when requested, writes metadata and tab-order fixes strictly to a separate `<stem>_patched.pdf` file (guarded against overwriting the source file).
- **Human sign-off required**: Never fabricates or auto-approves semantic `/Alt` descriptions for complex charts, diagrams, or medical/technical imagery. Final human review and live **NVDA or JAWS** screen-reader testing are mandatory before certifying legal PDF/UA, WCAG, or Section 508 compliance.

## License

[MIT](LICENSE)
