# Source-File Export and Acrobat Remediation Fixes

## Contents

- [When to Fix in Source vs. Acrobat](#when-to-fix-in-source-vs-acrobat)
- [Microsoft Word Export Fixes](#microsoft-word-export-fixes)
- [Adobe InDesign Export Fixes](#adobe-indesign-export-fixes)
- [Fixing Duplicate MCIDs (PAC 2026 Crashes)](#fixing-duplicate-mcids-pac-2026-crashes)
- [Acrobat Pro Manual Remediation Steps](#acrobat-pro-manual-remediation-steps)
- [Final Screen-Reader Verification Checklist](#final-screen-reader-verification-checklist)

## When to Fix in Source vs. Acrobat

Always fix structural issues in the source file (Microsoft Word or Adobe InDesign) whenever the source document is available:

- **Fix in source first**: Missing tag tree (`CRITICAL_UNTAGGED_PDF`), heading skips, untagged figures, missing table headers, and duplicate MCIDs caused by broken exports. Re-exporting takes minutes and preserves accessibility across future document revisions.
- **Use `scripts/pdf_ua_safe_patch.py`**: Safe non-destructive fixes for `/Lang`, `/Title` + XMP `dc:title`, `/DisplayDocTitle`, page `/Tabs /S`, and AcroForm `/Annots` visual reading order when the source file is locked or unavailable.
- **Use Acrobat Pro only as a final polish step**: Complex table `<TH>` scope attributes, artifacting decorative borders, and repairing stubborn tag-tree anomalies when source files no longer exist.

## Microsoft Word Export Fixes

Never use **File > Print > Microsoft Print to PDF** or **Adobe PDF Printer**; virtual printers flatten the document and strip `/StructTreeRoot`.

1. **Export tagged PDF properly**:

   - Windows: Use **File > Save As > PDF**, click **Options**, and verify **Document structure tags for accessibility** and **Document properties** are checked.
   - macOS: Use **File > Save As > PDF** and select **Best for electronic distribution and accessibility**.

2. **Fix heading hierarchy**:

   - Apply built-in `Heading 1`, `Heading 2`, and `Heading 3` styles sequentially without skipping levels.
   - Replace bolded normal paragraphs used as visual headers with true Heading styles.

3. **Fix figure alternative text**:

   - Right-click each image or chart, choose **View Alt Text**, and write concise functional descriptions.
   - Check **Mark as decorative** for pure visual flourishes so Word exports them as `/Artifact` rather than `<Figure>`.

4. **Fix table headers**:

   - Select the top header row, open **Table Layout > Repeat Header Rows**, and verify **Header Row** is enabled under **Table Design** so Word emits `<TH>` tags.

5. **Set document metadata**:

   - Open **File > Info > Properties** and populate **Title** before exporting.

## Adobe InDesign Export Fixes

1. **Export tagged Interactive or Print PDF**:

   - Choose **File > Export > Adobe PDF (Interactive)** (recommended for forms and digital reports) or **Adobe PDF (Print)** with **Create Tagged PDF** and **Use Structure for Tab Order** checked.

2. **Map Paragraph Styles to Export Tags**:

   - Open **Paragraph Styles > Edit All Export Tags**, select **PDF**, and map each heading style to `H1`, `H2`, `H3` without skipping levels.

3. **Control reading order with the Articles panel**:

   - Drag frames into **Window > Articles** in exact logical reading order and enable **Use for Tagging Order in Tagged PDF** from the panel menu.

4. **Add alt text and mark decorative art**:

   - Select each frame, open **Object > Object Export Options > Alt Text**, and enter custom alt text (or select **Tagged PDF > Apply Tag: Artifact** for decorative rules and backgrounds).

5. **Configure table header rows**:

   - Select header rows in InDesign tables and choose **Table > Convert Rows > To Header** so `<TH>` tags export cleanly.

6. **Set document title and language**:

   - Enter a human-readable title in **File > File Info > Document Title**, and assign paragraph language cleanly so `/Lang` exports to the PDF catalog.

## Fixing Duplicate MCIDs (PAC 2026 Crashes)

Duplicate `/MCID` numbers on the same page cause PAC 2026 to freeze or abort with "MCID already present":

1. **InDesign root cause**: Duplicate MCIDs frequently occur when master-page items, anchored objects, or pasted frames collide during PDF export. Re-export after ungrouping nested anchored frames, flattening transparency cleanly, and ensuring **Create Tagged PDF** runs on a clean `.idml` round-trip (`File > Export > InDesign Markup (IDML)`, reopen, then export PDF).
2. **Acrobat repair (when source is unavailable)**:

   - Open the **Tags** panel and locate the page and object IDs listed in `pdf_ua_audit.json` under `duplicate_mcids`.
   - Use **Find > Unmarked Content** or expand the duplicate `<P>` / `<Span>` / `<Figure>` nodes, delete the redundant empty container holding the duplicate MCID reference, and save with **File > Save As** (full rewrite rather than incremental save).

## Acrobat Pro Manual Remediation Steps

Use these steps when only the PDF exists and `pdf_ua_safe_patch.py` has already fixed metadata and form tab order:

- **Set Table Header Scope**: Open the **Tags** panel, right-click each `<TH>` cell inside flagged `<Table>` object IDs, choose **Properties > Tag**, verify Type is **Table Header Cell**, and set **Scope** (`Column` or `Row`) via the **Table Editor** in the Reading Order tool.
- **Fix Skipped Headings**: Right-click the flagged `<H3>` or `<H4>` tag in the **Tags** panel, open **Properties**, and change **Type** to the next sequential level (for example `Heading Level 2`).
- **Add Human-Reviewed Alt Text**: Right-click each flagged `<Figure>` tag, open **Properties > Tag**, and enter verified **Alternate Text** (or right-click the content child and choose **Change Tag to Artifact** if decorative).
- **Verify Form Tooltips**: Open **Prepare Form**, double-click each field listed in `missing_form_tooltips`, and populate **General > Tooltip** so `/TU` is set.

## Final Screen-Reader Verification Checklist

Before declaring legal compliance for PDF/UA-1, WCAG 2.1 AA, or Section 508, complete these manual checks:

1. Open the final PDF in Adobe Acrobat Reader and navigate end-to-end using **NVDA** (Windows) or **JAWS** (Windows).
2. Press `H` to confirm every heading announces at the expected level without skips.
3. Press `G` to confirm every meaningful figure announces accurate, human-reviewed alt text and decorative graphics stay silent.
4. Press `T` and use `Ctrl+Alt+Arrow` keys to confirm every data table announces column and row headers accurately.
5. Press `Tab` through every AcroForm field to confirm focus follows visual order and each field announces its `/TU` tooltip and input role.
