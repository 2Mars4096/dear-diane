# Website Consistency Audit — Super DAN

> Audit date: current session  
> Scope: entire `website/` directory  
> Status: **no files were modified** — this is a read-only review

---

## 1. Executive Summary

The website currently runs **three independent design systems** side-by-side. Nothing in `git status` shows any recent edits to website files, so the site has **not** been rewritten in this session.

The core inconsistency is that the landing page (`index.html`) and the greeting page (`hi.html`) each ship their own embedded CSS, while the app pages reference a shared `styles.css` that defines a third, different palette and typography system.

---

## 2. Design System Fragmentation

### System A — Landing page (`website/index.html`)
- **Palette:** cyan / electric blue (`#4cc9f0`, `#4361ee`)
- **Font:** system-ui stack (no custom font loaded)
- **Buttons:** `.btn` — `border-radius: 10px`, flat or gradient fill
- **Cards:** dark surface, `border-radius: 14px`
- **Background:** dark-blue radial gradients
- **Does NOT link to `styles.css`**

### System B — Greeting page (`website/hi.html`)
- **Palette:** pink / purple / cyan (`#7ce7ff`, `#ff78dc`, `#a78bfa`)
- **Font:** Syne + Space Mono (loaded from Google Fonts)
- **Buttons:** `.home-link` — pill shape (`border-radius: 999px`), glass morphism
- **Background:** deep-blue/purple radial gradients
- **Does NOT link to `styles.css`**

### System C — App shell (`website/styles.css` + `apps/index.html`)
- **Palette:** industrial orange / steel blue (`#c97b46`, `#5a8ab5`, `#d4a843`)
- **Font:** Barlow + Barlow Condensed + IBM Plex Mono (none loaded in HTML!)
- **Buttons:** `.btn` — pill shape (`border-radius: 999px`), gradient fill
- **Cards:** glass morphism (`backdrop-filter`, `rgba` backgrounds)
- **Cursor glow + parallax effects** defined in CSS but may not be wired up on all pages

---

## 3. Button Inconsistencies (operator’s original concern)

| Page | Button class | Shape | Colors | Font |
|------|-------------|-------|--------|------|
| `index.html` | `.btn.primary` | Rounded rect (10px) | Blue gradient | system-ui |
| `hi.html` | `.home-link` | Pill (999px) | Glass / white | Space Mono |
| `apps/index.html` | `.btn.btn-primary` | Pill (999px) | Orange gradient | Barlow (not loaded) |
| App detail pages | *(not audited)* | — | — | — |

**Finding:** Buttons vary in shape, color, font, and hover behavior across every top-level page.

---

## 4. Other Consistency Issues Found

1. **No shared CSS on landing or hi pages** — Both pages embed full styles in `<style>` tags instead of linking to `styles.css`.
2. **Missing font imports** — `styles.css` references Barlow, Barlow Condensed, and IBM Plex Mono, but no page was seen loading them via `<link>` (except `hi.html` which loads Syne/Space Mono for itself).
3. **Header/navigation drift** — `index.html` has a minimal brand-only header; `apps/index.html` has a sticky nav with 5 links. The nav links in `apps/index.html` point to `#about`, `#biology`, etc., but `index.html` does not contain those sections.
4. **Card styling differs** — `index.html` cards are dark solid; `styles.css` cards are glass with `backdrop-filter`.
5. **Footer present only on `index.html`** — Other pages lack a footer.

---

## 5. Recommended Next Steps

1. **Pick one design system** to own (the industrial theme in `styles.css` is the most complete).
2. **Refactor `index.html` and `hi.html`** to link the shared stylesheet instead of embedded CSS.
3. **Consolidate button classes** to a single `.btn` / `.btn-primary` pattern.
4. **Add missing Google Fonts `<link>`** tags wherever `styles.css` is used.
5. **Audit the 8 app detail pages** under `apps/*/` for their own embedded style drift.

---

## 6. File Change Log (this session)

**None.** No website source files were edited. This audit document is the only new file.
