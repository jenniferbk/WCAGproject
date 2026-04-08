# LaTeX UI & What's New Changelog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LaTeX as a first-class format in the UI and create a "What's New" changelog surface (banner + header link + modal) so users discover new features.

**Architecture:** All changes are in the single-page frontend (`src/web/static/index.html`) plus one new HTML fragment file (`src/web/static/changelog.html`). No backend changes. The changelog modal reuses the existing `infoModal` pattern. Unread tracking uses `localStorage`.

**Tech Stack:** Vanilla HTML/CSS/JS (matches existing codebase), `fetch()` for loading changelog fragment.

**Spec:** `docs/superpowers/specs/2026-04-06-latex-ui-and-changelog-design.md`

**Security note:** The changelog is loaded from our own `/static/changelog.html` (a trusted first-party file served by our own FastAPI static files middleware). This follows the same pattern used by the existing `infoContent` object which sets modal body content from inline JS strings. The content is author-controlled, not user-supplied.

---

### Task 1: Update meta tags and page description for LaTeX

**Files:**
- Modify: `src/web/static/index.html:7` (meta description)
- Modify: `src/web/static/index.html:13` (OG description)
- Modify: `src/web/static/index.html:21` (Twitter description)

- [ ] **Step 1: Update `<meta name="description">`**

In `src/web/static/index.html`, line 7, change:

```html
<meta name="description" content="AI-powered WCAG 2.1 AA accessibility remediation for university course materials. Upload Word, PDF, or PowerPoint files and get accessible versions back automatically.">
```

to:

```html
<meta name="description" content="AI-powered WCAG 2.1 AA accessibility remediation for university course materials. Upload Word, PDF, PowerPoint, or LaTeX files and get accessible versions back automatically.">
```

- [ ] **Step 2: Update OG description**

Line 13, change:

```html
<meta property="og:description" content="Make university course documents WCAG 2.1 AA accessible with AI. Supports Word, PDF, and PowerPoint.">
```

to:

```html
<meta property="og:description" content="Make university course documents WCAG 2.1 AA accessible with AI. Supports Word, PDF, PowerPoint, and LaTeX.">
```

- [ ] **Step 3: Update Twitter description**

Line 21, change:

```html
<meta name="twitter:description" content="Make university course documents WCAG 2.1 AA accessible with AI. Supports Word, PDF, and PowerPoint.">
```

to:

```html
<meta name="twitter:description" content="Make university course documents WCAG 2.1 AA accessible with AI. Supports Word, PDF, PowerPoint, and LaTeX.">
```

- [ ] **Step 4: Verify changes**

Open the page and inspect the `<head>` in DevTools to confirm all three meta tags updated.

- [ ] **Step 5: Commit**

```bash
git add src/web/static/index.html
git commit -m "feat: add LaTeX to meta descriptions (OG, Twitter, SEO)"
```

---

### Task 2: Update landing page (logged-out view) for LaTeX

**Files:**
- Modify: `src/web/static/index.html:2445` (format pill)
- Modify: `src/web/static/index.html:2454` (how-it-works step 1)

- [ ] **Step 1: Update format pill in landing hero**

Line 2445, change:

```html
          .docx .pdf .pptx
```

to:

```html
          .docx .pdf .pptx .tex
```

- [ ] **Step 2: Update how-it-works step 1**

Line 2454, change:

```html
        <div class="landing-step"><span class="landing-step-num">1</span> Upload your Word, PDF, or PowerPoint files</div>
```

to:

```html
        <div class="landing-step"><span class="landing-step-num">1</span> Upload your Word, PDF, PowerPoint, or LaTeX files</div>
```

- [ ] **Step 3: Verify by viewing logged-out landing page**

Sign out or open an incognito window and confirm the pills and step text updated.

- [ ] **Step 4: Commit**

```bash
git add src/web/static/index.html
git commit -m "feat: add LaTeX to landing page hero and how-it-works"
```

---

### Task 3: Update logged-in welcome section and upload area for LaTeX

**Files:**
- Modify: `src/web/static/index.html:2642` (welcome pill "3 formats")
- Modify: `src/web/static/index.html:2676-2696` (upload icon fan — add TEX and ZIP SVGs)
- Modify: `src/web/static/index.html:2700` (upload hint text)
- Modify: `src/web/static/index.html:2707` (file input accept attribute)

- [ ] **Step 1: Update welcome pill "3 formats" to "5 formats"**

Line 2642, change:

```html
          3 formats
```

to:

```html
          5 formats
```

- [ ] **Step 2: Add TEX and ZIP SVG icons to the upload icon fan**

After the PPTX icon SVG (after line 2696), add:

```html
          <!-- TEX icon -->
          <svg class="upload-file-icon" viewBox="0 0 48 56" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="2" y="2" width="44" height="52" rx="4" fill="#E8F5E9" stroke="#2E7D32" stroke-width="2"/>
            <rect x="10" y="36" width="28" height="10" rx="2" fill="#2E7D32"/>
            <text x="24" y="44" text-anchor="middle" fill="white" font-size="7" font-weight="700" font-family="system-ui">TEX</text>
            <path d="M14 14h20M14 20h16M14 26h12" stroke="#2E7D32" stroke-width="2" stroke-linecap="round"/>
          </svg>
          <!-- ZIP icon -->
          <svg class="upload-file-icon" viewBox="0 0 48 56" fill="none" xmlns="http://www.w3.org/2000/svg">
            <rect x="2" y="2" width="44" height="52" rx="4" fill="#F3E5F5" stroke="#7B1FA2" stroke-width="2"/>
            <rect x="10" y="36" width="28" height="10" rx="2" fill="#7B1FA2"/>
            <text x="24" y="44" text-anchor="middle" fill="white" font-size="7" font-weight="700" font-family="system-ui">ZIP</text>
            <path d="M14 14h20M14 20h16M14 26h12" stroke="#7B1FA2" stroke-width="2" stroke-linecap="round"/>
          </svg>
```

- [ ] **Step 3: Update upload hint text**

Line 2700, change:

```html
          <div class="upload-hint">.docx, .pdf, and .pptx files</div>
```

to:

```html
          <div class="upload-hint">.docx, .pdf, .pptx, .tex, and .zip files</div>
```

- [ ] **Step 4: Update file input accept attribute**

Line 2707, change:

```html
        <input type="file" id="fileInput" accept=".docx,.pdf,.pptx" multiple
```

to:

```html
        <input type="file" id="fileInput" accept=".docx,.pdf,.pptx,.tex,.ltx,.zip" multiple
```

- [ ] **Step 5: Verify upload area visually**

Log in and confirm: 5 format icons showing (DOCX, PDF, PPTX, TEX, ZIP), hint text updated, file picker allows .tex and .zip selection.

- [ ] **Step 6: Commit**

```bash
git add src/web/static/index.html
git commit -m "feat: add LaTeX/ZIP to upload area badges, hint, and accept attribute"
```

---

### Task 4: Update About and Credits modals for LaTeX

**Files:**
- Modify: `src/web/static/index.html:3796` (About — supported formats line)
- Modify: `src/web/static/index.html:3819` (About — after Limitations, before Questions)
- Modify: `src/web/static/index.html:3917-3923` (Credits — open source list)

- [ ] **Step 1: Update About modal supported formats line**

Line 3796, change:

```js
      <p>Supports Word documents (.docx), PDFs (.pdf), and PowerPoint presentations (.pptx).</p>
```

to:

```js
      <p>Supports Word documents (.docx), PDFs (.pdf), PowerPoint presentations (.pptx), and LaTeX files (.tex, .zip).</p>
```

- [ ] **Step 2: Add LaTeX & Math subsection to About modal**

After the `<h3>Limitations</h3>` paragraph (line 3820) and before `<h3>Questions?</h3>`, insert:

```
      <h3>LaTeX &amp; math</h3>
      <p>Upload <code>.tex</code> files directly, or <code>.zip</code> archives that include images and style files. The system converts LaTeX to structured HTML using <a href="https://dlmf.nist.gov/LaTeXML/" target="_blank" rel="noopener">LaTeXML</a>, renders equations as SVG with embedded MathML for screen reader access, and generates plain-language descriptions for complex math. Algorithms and pseudocode are preserved as formatted blocks.</p>
      <p>TikZ diagrams get placeholder descriptions noting the diagram structure. Full visual rendering of TikZ is planned for a future release.</p>
```

- [ ] **Step 3: Add LaTeXML and ziamath to Credits modal**

In the Open Source `<ul>` (around line 3917-3923), after the WeasyPrint entry and before the Fraunces entry, add:

```
        <li><strong>LaTeXML</strong> &mdash; LaTeX to HTML/MathML conversion</li>
        <li><strong>ziamath</strong> &mdash; MathML to SVG rendering</li>
```

- [ ] **Step 4: Verify modals**

Click About and Credits in the footer and confirm the new content appears correctly.

- [ ] **Step 5: Commit**

```bash
git add src/web/static/index.html
git commit -m "feat: add LaTeX info to About and Credits modals"
```

---

### Task 5: Create changelog HTML fragment

**Files:**
- Create: `src/web/static/changelog.html`

This task creates the changelog entries file. The content will be written using the `humanize` skill for natural language, but the structure is defined here.

- [ ] **Step 1: Write changelog.html**

Create `src/web/static/changelog.html` with the following structure. The `data-latest-version` attribute on the root div is read by the JS to determine the latest version for dot tracking.

```html
<div class="changelog" data-latest-version="2026-04-06-1">

  <article class="changelog-entry">
    <div class="changelog-meta">
      <time datetime="2026-04-06">April 6, 2026</time>
      <span class="changelog-tag new-feature">New feature</span>
    </div>
    <h3>LaTeX & Math Support</h3>
    <p>You can now upload .tex files (or .zip archives with images and styles) and get back accessible PDFs. Equations are rendered as SVG images with MathML baked in, so screen readers can describe them. Complex math gets a plain-language description generated by AI. Algorithms, pseudocode, and TikZ diagrams are handled too.</p>
  </article>

  <article class="changelog-entry">
    <div class="changelog-meta">
      <time datetime="2026-04-05">April 5, 2026</time>
      <span class="changelog-tag quality">Quality improvement</span>
    </div>
    <h3>Better Scanned PDF Results</h3>
    <p>Scanned PDFs now go through a three-step process: text extraction, structural analysis, and error correction. Tables, headings, and reading order in scanned documents are significantly more accurate than before. If you tried a scanned PDF earlier and weren't happy with the result, it's worth running it again.</p>
  </article>

  <article class="changelog-entry">
    <div class="changelog-meta">
      <time datetime="2026-04-05">April 5, 2026</time>
      <span class="changelog-tag quality">Quality improvement</span>
    </div>
    <h3>Visual Quality Checks</h3>
    <p>After remediating a scanned PDF, the system now compares the original pages against the output to catch anything that got lost in translation — missing text, dropped tables, or content that shifted. Findings show up in your compliance report with side-by-side thumbnails.</p>
  </article>

  <article class="changelog-entry">
    <div class="changelog-meta">
      <time datetime="2026-02-24">February 24, 2026</time>
      <span class="changelog-tag new-feature">New feature</span>
    </div>
    <h3>Page-Based Pricing</h3>
    <p>Credits are now counted by page instead of by document. A 2-page assignment costs less than a 50-page course packet. You can see exactly how many pages you have left in the balance bar at the top of the page.</p>
  </article>

  <article class="changelog-entry">
    <div class="changelog-meta">
      <time datetime="2026-02-20">February 20, 2026</time>
      <span class="changelog-tag new-feature">New feature</span>
    </div>
    <h3>Password Reset</h3>
    <p>Forgot your password? There's now a "Forgot password?" link on the sign-in form that sends a reset link to your email. No more emailing support to get back into your account.</p>
  </article>

</div>
```

**Note:** The paragraph text above is placeholder — invoke the `humanize` skill on this file after creation to rewrite in natural voice. The structure and dates are final.

- [ ] **Step 2: Verify the file is served**

Run the dev server and confirm `http://localhost:8000/static/changelog.html` returns the HTML fragment.

- [ ] **Step 3: Commit**

```bash
git add src/web/static/changelog.html
git commit -m "feat: add changelog.html with initial release entries"
```

---

### Task 6: Add changelog CSS to index.html

**Files:**
- Modify: `src/web/static/index.html` (add CSS before the `</style>` closing tag, around line 2407)

- [ ] **Step 1: Add changelog CSS**

Before the `</style>` tag (line 2407), insert the CSS for: `#changelogBanner` (hidden by default, flex layout, accent-light bg, 3px accent left border, visible when `.visible` class added), `.changelog-banner-text` (flex: 1), `.changelog-banner-link` (accent color, underline, button reset), `.changelog-banner-dismiss` (no bg/border, tertiary color), `.whats-new-link` (accent color, relative position), `.whats-new-link.has-unread::after` (8px green dot, absolute top-right), `.changelog` / `.changelog-entry` (padding, bottom border separator), `.changelog-meta` (flex row, gap), `.changelog-meta time` (small, tertiary), `.changelog-tag` (small pill, uppercase), `.changelog-tag.new-feature` (accent-light bg, accent color), `.changelog-tag.quality` (success-bg, success color), `.changelog-tag.new-format` (warning-bg, warning color), `.changelog-entry h3` (display font, 1.05rem), `.changelog-entry p` (0.9rem, secondary color), `.changelog-entry code` (warm bg, small radius).

- [ ] **Step 2: Commit**

```bash
git add src/web/static/index.html
git commit -m "feat: add CSS for changelog banner, header link, and entry styles"
```

---

### Task 7: Add changelog banner and header link HTML

**Files:**
- Modify: `src/web/static/index.html:2416-2423` (header — add What's New link)
- Modify: `src/web/static/index.html:2556` (after `<div id="appView">` — add banner)

- [ ] **Step 1: Add "What's New" link to header**

In the header's `.header-right` div, after the `<span>` subtitle and before the admin link (between lines 2417 and 2418), insert:

```html
    <a class="whats-new-link hidden" id="whatsNewLink" onclick="openChangelog(event)" role="button" tabindex="0">What's New</a>
```

So the header-right becomes:

```html
  <div class="header-right">
    <span class="header-subtitle" id="headerSubtitle">WCAG 2.1 AA Document Accessibility</span>
    <a class="whats-new-link hidden" id="whatsNewLink" onclick="openChangelog(event)" role="button" tabindex="0">What's New</a>
    <a class="admin-link hidden" id="adminLink" onclick="showAdminView()" role="button" tabindex="0">Admin</a>
    <div class="user-menu hidden" id="userMenu">
      <span class="user-menu-name" id="userDisplayName"></span>
      <button class="btn-logout" onclick="handleLogout()" aria-label="Sign out">Sign out</button>
    </div>
  </div>
```

- [ ] **Step 2: Add changelog banner HTML**

After `<div id="appView" class="hidden">` (line 2556) and before the Account Status Card (line 2558), insert:

```html
    <!-- Changelog banner -->
    <div id="changelogBanner" role="status">
      <div class="changelog-banner-text">
        <strong id="changelogBannerTitle"></strong> &mdash;
        <span id="changelogBannerSummary"></span>
        <button class="changelog-banner-link" onclick="openChangelog(event)">See what's new</button>
      </div>
      <button class="changelog-banner-dismiss" onclick="dismissChangelog()" aria-label="Dismiss">&times;</button>
    </div>
```

- [ ] **Step 3: Verify HTML in DevTools**

Inspect the page — confirm the banner div exists (hidden) and the header link exists (hidden). Both will be shown by JS in the next task.

- [ ] **Step 4: Commit**

```bash
git add src/web/static/index.html
git commit -m "feat: add changelog banner and What's New header link HTML"
```

---

### Task 8: Add changelog JavaScript logic

**Files:**
- Modify: `src/web/static/index.html` — add JS constants, functions, and modify `showAppView()`

- [ ] **Step 1: Add CHANGELOG_LATEST constant**

Near the top of the `<script>` block (after the existing global variables like `let currentUser = null;`), add:

```js
// ── Changelog ──
const CHANGELOG_LATEST = '2026-04-06-1';
const CHANGELOG_KEY = 'changelog_seen';
```

- [ ] **Step 2: Add changelog functions**

Before the `// ── Info modals` section (around line 3787), add the following functions:

`hasUnreadChangelog()` — returns `true` if `localStorage.getItem(CHANGELOG_KEY) !== CHANGELOG_LATEST`

`markChangelogRead()` — sets localStorage to CHANGELOG_LATEST, removes `has-unread` class from `whatsNewLink`, removes `visible` class from `changelogBanner`

`dismissChangelog()` — calls `markChangelogRead()`

`updateChangelogIndicators()` — adds or removes `has-unread` class on `whatsNewLink` based on `hasUnreadChangelog()`

`showChangelogBanner()` — if `hasUnreadChangelog()`, sets banner title to `'LaTeX & Math Support'`, sets banner summary to `'Upload .tex files and get accessible PDFs with rendered equations.'`, adds `visible` class to `changelogBanner`

`openChangelog(e)` — prevents default, sets infoModal title to `"What's New"`, sets body to a loading message, adds `active` class to infoModal, fetches `/static/changelog.html`, sets body to the response text (this is a trusted first-party static file, same pattern as existing `infoContent` which also sets body from author-controlled strings), calls `markChangelogRead()`. On fetch error, shows an error message in the body.

- [ ] **Step 3: Wire changelog into showAppView()**

In the `showAppView()` function (line 2945), after the existing `document.getElementById('adminLink').classList.toggle('hidden', !currentUser.is_admin);` line, add:

```js
  // Show What's New link and check for unread
  document.getElementById('whatsNewLink').classList.remove('hidden');
  updateChangelogIndicators();
  showChangelogBanner();
```

- [ ] **Step 4: Test the full flow**

1. Clear `localStorage.removeItem('changelog_seen')` in DevTools console
2. Refresh — banner should appear, header dot should show
3. Click "See what's new" — modal opens with changelog entries, dot disappears, banner hides
4. Refresh again — no banner, no dot (already seen)
5. Change `CHANGELOG_LATEST` to a new value — banner and dot return

- [ ] **Step 5: Commit**

```bash
git add src/web/static/index.html
git commit -m "feat: add changelog JS — banner, header dot, modal fetch, localStorage tracking"
```

---

### Task 9: Humanize changelog content

**Files:**
- Modify: `src/web/static/changelog.html`

- [ ] **Step 1: Invoke humanize skill on changelog.html**

Use the `humanize` skill to review and rewrite the changelog entry paragraphs in `src/web/static/changelog.html`. The dates, headings, tags, and HTML structure stay the same — only the `<p>` content gets rewritten for natural, conversational voice.

- [ ] **Step 2: Update banner summary text**

After humanizing, update the banner summary string in the `showChangelogBanner()` function in `index.html` to match the rewritten first entry's tone. The summary should be one short sentence.

- [ ] **Step 3: Commit**

```bash
git add src/web/static/changelog.html src/web/static/index.html
git commit -m "docs: humanize changelog entry language"
```

---

### Task 10: Final verification and deploy

**Files:**
- No code changes — verification only

- [ ] **Step 1: Run existing tests to confirm no regressions**

```bash
cd /Users/jenniferkleiman/Documents/GitHub/WCAGproject
pytest tests/test_web_auth.py tests/test_auth.py tests/test_users.py -v
```

Expected: All pass. (Frontend-only changes shouldn't break backend tests, but verify.)

- [ ] **Step 2: Visual check — logged-out view**

Open incognito — confirm: format pill shows ".docx .pdf .pptx .tex", how-it-works step says "Word, PDF, PowerPoint, or LaTeX files".

- [ ] **Step 3: Visual check — logged-in view**

Log in — confirm: "5 formats" pill, 5 upload icons (DOCX, PDF, PPTX, TEX, ZIP), upload hint shows all formats, file picker accepts .tex/.zip.

- [ ] **Step 4: Visual check — changelog flow**

Clear localStorage — refresh — confirm banner appears — click "See what's new" — modal opens with entries — dismiss — refresh — no banner or dot.

- [ ] **Step 5: Visual check — About and Credits modals**

Click About — confirm LaTeX section appears. Click Credits — confirm LaTeXML and ziamath in list.

- [ ] **Step 6: Deploy to production**

```bash
ssh -i ~/.ssh/oracle_cloud ubuntu@150.136.101.132
cd /home/ubuntu/a11y-remediate
git pull
sudo systemctl restart a11y-remediate
```

- [ ] **Step 7: Verify on live site**

Visit `https://remediate.jenkleiman.com/` and repeat steps 2-5 on production.
