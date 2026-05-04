# Astral Website

The Astral.us website - autonomous drone platform. Built with Next.js 14, Tailwind CSS, Shadcn/ui, and deployed to AWS via SST.

## Tech Stack

- **Framework**: Next.js 14 (App Router)
- **Styling**: Tailwind CSS + Shadcn/ui
- **CMS**: Sanity.io
- **Infrastructure**: SST (AWS Lambda, CloudFront, S3, DynamoDB)
- **Documentation**: Mintlify

## Getting Started

### Prerequisites

- Node.js 20+
- npm
- AWS CLI configured (for deployment)

### Installation

```bash
# Clone the repository
git clone https://github.com/astral-us/astral-website.git
cd astral-website

# Install dependencies
npm install

# Copy environment variables
cp .env.example .env.local

# Start development server
npm run dev
```

### Environment Variables

Create a `.env.local` file with:

```bash
# Sanity
NEXT_PUBLIC_SANITY_PROJECT_ID=your-project-id
NEXT_PUBLIC_SANITY_DATASET=production

# Base URL
NEXT_PUBLIC_BASE_URL=http://localhost:3000
```

## Project Structure

```
astral-website/
├── src/
│   ├── app/                 # Next.js App Router pages
│   ├── components/          # React components
│   │   ├── layout/          # Header, Footer
│   │   ├── sections/        # Homepage sections
│   │   └── ui/              # Shadcn components
│   └── lib/                 # Utilities, products
├── infra/                   # SST infrastructure
│   ├── api.ts               # Lambda functions
│   ├── database.ts          # DynamoDB
│   ├── storage.ts           # S3
│   └── web.ts               # Next.js site
├── functions/               # Lambda handlers
├── sanity/                  # Sanity CMS schemas
├── docs/                    # Mintlify documentation
└── sst.config.ts            # SST configuration
```

## Development

```bash
# Run Next.js development server
npm run dev

# Run with SST (includes AWS resources)
npm run sst:dev
```

## Deployment

### Deploy to AWS

```bash
# Deploy to staging
npm run sst:deploy -- --stage staging

# Deploy to production
npm run sst:deploy -- --stage production
```

### Remove

```bash
# Remove a stage
npm run sst:remove -- --stage staging
```

## Sanity CMS

### Setup

1. Create a project at [sanity.io](https://sanity.io)
2. Add your project ID to `.env.local`
3. Run the Sanity Studio:

```bash
npx sanity dev
```

### Content Types

- **Blog Posts**: Articles with rich text, images, code blocks
- **Products**: Drone products with specs and pricing
- **Solutions**: Industry-specific pages
- **Case Studies**: Customer success stories
- **Team Members**: Company team
- **FAQs**: Frequently asked questions

## Mintlify Docs

Documentation is in the `/docs` directory. To preview:

```bash
npx mintlify dev
```

Deploy docs separately to Mintlify or host at `docs.astral.us`.

## Testing

```bash
# Lint
npm run lint

# Type check
npx tsc --noEmit

# Build
npm run build
```

## Infrastructure

### AWS Resources (via SST)

- **CloudFront**: CDN for the website
- **Lambda**: SSR and API routes
- **S3**: Static assets and product images
- **DynamoDB**: Orders table
- **SES**: Transactional emails (requires domain verification)

### Costs (Estimated)

- Low traffic (<100k/mo): ~$5-15/month
- Medium traffic (<1M/mo): ~$20-50/month
- High traffic: Contact for enterprise pricing

## Contributing

1. Create a feature branch
2. Make changes
3. Run `npm run lint` and `npm run build`
4. Submit a pull request

## License

Proprietary - Astral, Inc.
