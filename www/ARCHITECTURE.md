# presidioautonomy.com — Architecture Overview

## One repo, one source of truth

The live website at **https://presidioautonomy.com** is built entirely from this directory
(`eco/www/`). It's a Next.js 16 App Router site deployed to AWS via SST (CloudFront + Lambda).
GitHub repo: `presidio-autonomy/eco`, path `www/`.

No CMS, no headless WordPress, no Sanity (an earlier scaffold existed and was removed —
half-wired dependencies are worse than none). All content lives as TypeScript/MDX files
under `src/`.

---

## Where content lives

| Content | File |
|---|---|
| Blog post metadata (title, date, slug) | `src/lib/blog-data.ts` |
| Blog post bodies | `src/app/blog/post-bodies.tsx` |
| Site-wide constants (URLs, org links, email) | `src/lib/site.ts` |
| Nav links | `src/components/layout/header.tsx`, `footer.tsx` |

## Routes

| URL | Purpose |
|---|---|
| `/` | Home — the "waypoints ≠ autonomy" pitch |
| `/autonomy` | Flagship explainer: levels, the metric gap, the separation principle, sim-first evaluation, FAQ |
| `/stack` | The open-source stack — presidio-sdk, eco, presidio-docs, with real GitHub links |
| `/get-started` | Sim-first quickstart (SDK / SITL / Isaac Sim) |
| `/blog`, `/blog/[slug]` | 2 seed posts, adapted from `eco/papers/` |
| `/about` | Why the project exists, contact |

There is deliberately no `/products`, `/pricing`, `/research`, `/operator`, `/docs`, `/privacy`,
or `/terms` — this is not the old astral.us site. Hardware docs / SDK docs live in the
`presidio-docs` repo, not this site.

## What this repo is NOT

- **`eco/papers/`** — raw markdown source files that some blog posts are adapted from. Not
  wired into the website directly; the website has its own hand-written post bodies.
- **`presidio-autonomy/presidio-docs`** — SDK/API documentation (Mintlify). No blog, no
  marketing content, no shared components with this site.

## Datasets

The Yonder dataset is hosted on HuggingFace under a legacy namespace
(`https://huggingface.co/datasets/astralhf/yonder`) — see `SITE.yonderDataset` in
`src/lib/site.ts`. Not renamed; a real HuggingFace org migration hasn't happened.
