# Presidio Website

The astral.us marketing site. Next.js + Tailwind + Shadcn/ui, deployed to AWS via SST (OpenNext → CloudFront + Lambda + S3).

The site is informational only — there is no cart, no checkout, no payment processing. Hardware enquiries route through `/enterprise`.

## Tech Stack

- **Framework**: Next.js (App Router)
- **Styling**: Tailwind CSS + Shadcn/ui
- **CMS**: Sanity.io
- **Infrastructure**: SST (CloudFront, Lambda, S3) via OpenNext
- **Documentation**: Mintlify (separate repo: `presidio-autonomy/presidio-docs`)

## Getting Started

```bash
npm install
cp .env.example .env.local   # if/when env vars are needed
npm run dev
```

### Environment Variables

```bash
# Sanity
NEXT_PUBLIC_SANITY_PROJECT_ID=your-project-id
NEXT_PUBLIC_SANITY_DATASET=production

# Base URL (set automatically in deployed stages)
NEXT_PUBLIC_BASE_URL=http://localhost:3000
```

## Project Structure

```
www/
├── src/
│   ├── app/                 # App Router pages
│   ├── components/          # React components (layout, sections, ui)
│   └── lib/                 # Utilities, products catalog
├── infra/
│   ├── storage.ts           # S3 assets bucket
│   └── web.ts               # Next.js site (OpenNext)
├── public/                  # Static assets, including /docs/simulation/*.mp4
├── sanity/                  # Sanity CMS schemas
└── sst.config.ts            # SST configuration
```

## Deployment

See `CLAUDE.md` in this directory for the full deploy + git workflow. Short version:

```bash
AWS_PROFILE=astral npx sst deploy --stage production
```

### Remove

```bash
AWS_PROFILE=astral npx sst remove --stage <stage>
```

## Sanity CMS

```bash
npx sanity dev
```

Content types: Blog Posts, Products, Solutions, Case Studies, Team Members, FAQs.

## Testing

```bash
npm run lint
npx tsc --noEmit
npm run build
```

## License

Proprietary — Presidio, Inc.
