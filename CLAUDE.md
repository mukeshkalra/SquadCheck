# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SquadCheck is a static single-page application (SPA) — a World Cup 2026 companion site at [squadcheck.club](https://squadcheck.club). It features team rankings, daily quizzes (coming soon), and a pre-launch email capture.

The entire frontend lives in a single file: **`index.html`** — no build system, no dependencies, no package manager.

## Architecture

Everything is self-contained in `index.html`:

- **CSS**: Inline `<style>` block using CSS custom properties (`--lime`, `--dark`, etc.) for theming
- **HTML**: Semantic sections — `<nav>`, hero, stats bar, index table, features grid, ticker, email capture, footer
- **JavaScript**: Inline `<script>` at the bottom handling:
  - Live countdown timer to `2026-06-11T00:00:00Z` (World Cup kickoff)
  - `IntersectionObserver` for `.fade-in` scroll animations
  - `handleSubmit()` — stores emails to `localStorage` under key `squadcheck_emails`

## Design System

| Token | Value | Usage |
|-------|-------|-------|
| `--lime` | `#CCFF00` | Primary accent, CTAs, highlights |
| `--dark` | `#0A0A0C` | Page background |
| `--dark-2/3/4` | `#111114` / `#1A1A1F` / `#252530` | Card backgrounds, borders |
| `--white` | `#F0F0F2` | Body text |
| `--gray` | `#8A8A96` | Secondary text, labels |
| `--coral` | `#FF6B4A` | Error states |
| `--blue` | `#4A9EFF` | Accent (glow orb) |

Fonts: `Outfit` (body/headings) and `DM Mono` (numbers, scores) loaded from Google Fonts.

## Development

No build step — open `index.html` directly in a browser or serve with any static file server:

```bash
python3 -m http.server 8080
# or
npx serve .
```

## Key Notes

- Email submissions are stored **only in `localStorage`** — no backend. This is intentional for the pre-launch phase.
- The Readiness Index table is hardcoded HTML (top 5 teams shown; full 48-team rankings are "coming soon").
- The scrolling ticker (`ticker-track`) duplicates team names to create a seamless CSS loop.
- `logo.png` is referenced in three places: nav, hero, and footer.
