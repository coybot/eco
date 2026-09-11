# Presidio Website

The presidioautonomy.com site: an open-source drone/rover autonomy stack, and the argument that
waypoint-following isn't autonomy. Next.js + Tailwind + shadcn/ui, deployed to AWS via SST
(OpenNext → CloudFront + Lambda + S3).

Informational only — no cart, no checkout, no payment processing, no operator console.

## Tech Stack

- **Framework**: Next.js 16 (App Router)
- **Styling**: Tailwind CSS v4 + shadcn/ui
- **Infrastructure**: SST (CloudFront, Lambda, S3) via OpenNext
- **Documentation**: separate repo, `presidio-autonomy/presidio-docs` (Mintlify)

## Getting Started

```bash
npm install
npm run dev
```

Opens on `http://localhost:3000`.

## Project Structure

```
www/
├── src/
│   ├── app/                 # App Router pages: /, /autonomy, /stack, /get-started, /blog, /about
│   ├── components/          # layout (header/footer), sections, ui (shadcn primitives)
│   └── lib/                 # site.ts (constants), blog-data.ts, utils.ts
├── infra/
│   ├── storage.ts           # S3 assets bucket
│   └── web.ts               # Next.js site (OpenNext) + domain config
├── public/                  # Static assets — everything under public/media/, see CLAUDE.md
└── sst.config.ts            # SST configuration
```

## Deployment

See `CLAUDE.md` in this directory for the full deploy + git workflow. Short version:

```bash
just deploy-dev    # https://dev.presidioautonomy.com
just deploy-prod   # https://presidioautonomy.com
```

## Testing

```bash
npm run lint
npx tsc --noEmit
npm run build
```

## License

Code is MIT/Apache-2.0 — see the repo's LICENSE/NOTICE.
