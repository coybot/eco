# coy.bot — Architecture Overview

## One repo, one source of truth

The live website at **https://coy.bot** is built entirely from this directory (`eco/www/`).
It is a Next.js 16 App Router site deployed to AWS via SST (CloudFront + Lambda).
GitHub repo: `coybot/eco`, path `www/`.

There is no separate CMS, no headless WordPress, no Sanity, no external blog platform.
All content lives as TypeScript files in `src/`.

---

## Where content lives

| Content | File |
|---|---|
| Blog post metadata (title, date, slug, category) | `src/lib/blog-data.ts` |
| Blog post full bodies | `src/app/blog/post-bodies.tsx` |
| Research paper metadata (title, venue, dates, TL;DR, stats) | `src/lib/research-data.ts` |
| Research paper full bodies | `src/app/research/paper-bodies.tsx` |
| Product specs | `src/lib/products.ts` |
| Site-wide constants (URLs, org links) | `src/lib/site.ts` |

## Routes

| URL | Source file |
|---|---|
| `/research` | `src/app/research/page.tsx` — lists all 12 items (7 papers + 5 blog-only posts), sorted by date |
| `/blog` | `src/app/blog/page.tsx` |
| `/blog/[slug]` | `src/app/blog/[slug]/page.tsx` — renders body from `post-bodies.tsx`; if a companion paper exists, renders the full paper inline at `#paper` |
| `/research/[slug]` | 301 redirects to `/blog/[companionPostSlug]#paper` (see `next.config.ts`) |
| `/benchmark` | `src/app/benchmark/page.tsx` |
| `/datasets/yonder` | `src/app/datasets/yonder/page.tsx` |

## The 12 items on /research

Seven have both a paper and a blog write-up (blog post is canonical, paper embedded at `#paper`):

| Paper slug | Companion blog slug |
|---|---|
| `yonder` | `yonder-drone-navigation-dataset` |
| `metric-gap` | `metric-gap-vision-language-drone-navigation` |
| `engineering-separation` | `engineering-drone-autonomy-18-iterations` |
| `scaling-separation` | `drone-swarm-sensing-1000-drones` |
| `gemma4-pilot` | `why-vlm-drones-cant-beat-hovering` |
| `counter-uas` | `counter-uas-drone-attack-defense-simulation` |
| `droneport-atc` | `droneport-atc-tower-vs-selforg` |

Five are blog-only posts (no companion paper):

- `four-models-drone-autonomy`
- `domain-detector-aerial-autonomy`
- `domain-detector-aerial-autonomy-paper`
- `human-in-loop-drone-autonomy-94-percent`
- `how-to-make-autonomous-drones-smarter`

## Other repos — what they are NOT

- **`eco/papers/`** — raw markdown source files, not wired into the website. Pre-dates the current site. Not published anywhere automatically.
- **`coybot/coybot-docs`** — API/SDK documentation only, served at `docs.coy.bot`. No blog, no research section.

## Models

Drone inference models are hosted at `https://huggingface.co/coybothf/coybot-drone-models`.
Blog posts that reference models link there (not to S3). Constant defined in `src/lib/site.ts` as `SITE.droneModels`.
