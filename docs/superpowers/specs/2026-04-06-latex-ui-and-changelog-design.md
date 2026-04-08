# LaTeX UI Update & What's New Changelog

**Date:** 2026-04-06
**Status:** Design approved

Two independent frontend features, both in `src/web/static/index.html` (plus one new file).

---

## Feature 1: LaTeX Support in the UI

The backend already accepts `.tex`, `.ltx`, and `.zip` uploads (shipped 2026-04-05). The frontend doesn't reflect this anywhere. Update all touchpoints to treat LaTeX as a first-class format alongside DOCX, PDF, and PPTX.

### Changes (all in `index.html`)

**File input:**
- `accept` attribute: `.docx,.pdf,.pptx` → `.docx,.pdf,.pptx,.tex,.ltx,.zip`

**Upload area:**
- Add `TEX` and `ZIP` badges next to the existing DOCX/PDF/PPTX badges (same markup and style)
- Subtitle: ".docx, .pdf, .pptx, .tex, and .zip files"

**Landing page (logged-out hero):**
- Format pill: ".docx .pdf .pptx" → ".docx .pdf .pptx .tex .zip"
- "3 formats" feature pill → "5 formats"
- How-it-works step 1: "Upload your Word, PDF, PowerPoint, or LaTeX files"

**Meta tags:**
- `<meta name="description">`: add LaTeX mention
- OG description: add LaTeX mention
- Twitter description: add LaTeX mention

**About modal (`infoContent.about`):**
- Update supported formats line to include LaTeX
- Add a "LaTeX & math" subsection explaining: .tex upload, MathML→SVG rendering, AI-generated equation descriptions, algorithm/pseudocode support, TikZ diagram placeholders

**Credits modal (`infoContent.credits`):**
- Add LaTeXML and ziamath to the Open Source list

---

## Feature 2: What's New (Banner + Header Link + Modal)

A changelog surface so users can discover new features and improvements. Three pieces: a dismissible login banner, a persistent header link with notification dot, and a modal showing the full changelog.

### Changelog File

**New file: `src/web/static/changelog.html`**

An HTML fragment (not a full document) containing all changelog entries. Fetched by the modal via `fetch('/static/changelog.html')` and injected as innerHTML.

Entry structure:
```html
<article class="changelog-entry">
  <div class="changelog-meta">
    <time datetime="2026-04-06">April 6, 2026</time>
    <span class="changelog-tag new-feature">New feature</span>
  </div>
  <h3>LaTeX & Math Support</h3>
  <p>Description in plain, human language...</p>
</article>
```

Tag types with corresponding CSS classes:
- "New feature" → `.new-feature`
- "Quality improvement" → `.quality`
- "New format" → `.new-format`

Entries are ordered newest first.

**Initial entries (content to be written with humanize skill):**
1. LaTeX & Math Support (April 2026) — .tex/.zip upload, MathML→SVG, AI equation descriptions
2. Improved Scanned PDF Quality (April 2026) — hybrid OCR, better tables and text accuracy
3. Visual Quality Checks (April 2026) — AI compares original vs output, catches content gaps
4. Page-Based Pricing (February 2026) — replaced per-document limits with per-page credits
5. Password Reset (February 2026) — forgot password flow via email

### Version Tracking

**Constant in `index.html`:**
```js
const CHANGELOG_LATEST = '2026-04-06-1';
```

Updated whenever a new entry is added to `changelog.html`. This avoids a network request for the dot check on page load.

**localStorage key:** `changelog_seen`
- Stores the version string of the latest entry the user has seen
- Compared against `CHANGELOG_LATEST` to determine unread state

**JS functions:**
- `hasUnreadChangelog()` — returns `true` if `localStorage.getItem('changelog_seen') !== CHANGELOG_LATEST`
- `markChangelogRead()` — sets `localStorage` to `CHANGELOG_LATEST`, hides dot, hides banner

### Banner

**Placement:** Below the header, above the usage/balance bar. Inside `<main>`, first child when visible.

**HTML:** `<div id="changelogBanner">` — hidden by default.

**Content:** Latest entry's title + one-line summary + "See what's new" link + dismiss "×" button.

**Behavior:**
- Shown after login if `hasUnreadChangelog()` is true
- "See what's new" opens the changelog modal
- "×" calls `markChangelogRead()` and hides the banner
- Not shown if user has seen the latest version

**Styling:**
- `--accent-light` background, `--accent` 3px left border
- Full width within the main content column
- Title in semibold, summary in regular weight, dismiss on the right

### Header Link

**Placement:** In the header bar, before the Admin button / user name area.

**HTML:** `<a id="whatsNewLink">What's New</a>` with a `::after` pseudo-element green dot.

**Behavior:**
- Dot visible when `hasUnreadChangelog()` is true
- Clicking opens the changelog modal and calls `markChangelogRead()`
- Dot uses class toggle: `.has-unread` on the link controls dot visibility

### Modal

**Reuses the existing `infoModal`** (same overlay, close button, width, animation).

**On open:**
1. Set modal title to "What's New"
2. Show a loading state in the body
3. `fetch('/static/changelog.html')` → inject response as innerHTML
4. Call `markChangelogRead()`

**Content:** Scrollable, newest first. Each entry is an `<article>` with date, tag, heading, and description.

**Close:** Same as other modals — ×, click outside, Escape key.

### CSS Additions (in `index.html` `<style>`)

```
/* Changelog banner */
#changelogBanner — accent-light bg, left border, flex layout, hidden by default

/* Changelog entries */
.changelog-entry — bottom border separator, padding
.changelog-meta — flex row, date + tag
.changelog-tag — small pill, colored per type
.changelog-tag.new-feature — accent color
.changelog-tag.quality — success color
.changelog-tag.new-format — warning color

/* Header dot */
#whatsNewLink — positioned relative
#whatsNewLink.has-unread::after — small green dot, absolute positioned top-right
```

---

## Files Changed

| File | Change |
|------|--------|
| `src/web/static/index.html` | LaTeX in all copy/meta/accept, banner HTML, header link, changelog modal logic, CSS, `CHANGELOG_LATEST` constant |
| `src/web/static/changelog.html` | **New file** — changelog entries as HTML fragment |

## Out of Scope

- Server-side changelog tracking (using localStorage instead)
- Changelog as a separate routed page (using modal pattern)
- RSS feed or email notifications for updates
- Admin UI for editing changelog (hand-edit the HTML file)
