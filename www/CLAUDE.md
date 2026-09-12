# coy.bot — deploy + git workflow

This is the marketing site at https://coy.bot. It is **informational only**: there is no cart, no checkout, no payment processing, no financing, no orders flow. Any agent (Claude, Cursor, future me) that wants to "add" any of those should stop and ask the user — they have been removed deliberately and should stay out.

## What lives where

| Concern | Location |
|---|---|
| Source code | `coybot/eco` GitHub repo, this directory (`www/`) |
| Production site | AWS, deployed via SST → CloudFront + Lambda + S3 |
| Stage | `production` (live at https://coy.bot) |
| AWS profile | `coybot` (account `041686205727`, IAM user `yusuf`) |
| Git identity | `yusuf-coybot <218167113+yusuf-coybot@users.noreply.github.com>` (set as repo-local config) |

## The single most important rule

**Pushing to GitHub should trigger a deploy via GitHub Actions CI, but that workflow has not been set up yet.** Until it is, a push updates only the repo and you must deploy manually. AWS prod is updated only when someone runs `sst deploy` from a working tree.

This means GitHub `main` and prod can drift. When they do, the source of truth for *what users see* is prod, and the source of truth for *what's checked in* is `main`. Reconciling them is a deliberate action, not automatic.

**TODO:** Add a `.github/workflows/deploy.yml` that runs `AWS_PROFILE=coybot npx sst deploy --stage prod` on push to `main`, with `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` set as GitHub Actions secrets for the `coybot` IAM user.

## Deploy

From `www/`:

```bash
npm install                                          # if first time or deps changed
AWS_PROFILE=coybot npx sst deploy --stage prod
```

The deploy:
1. Runs `next build` (OpenNext picks up the result).
2. Uploads everything in `public/` to the AssetsBucket (S3) and serves via CloudFront.
3. Updates the Next.js Lambda with the SSR/route handlers.
4. Invalidates CloudFront paths that changed.

Typical run: 4–8 min. Watch for Pulumi errors near the end — exit code 0 does not always mean success; scan the tail for `Error` or `Failed`.

If the deploy errors talk about secrets (`StripeSecretKey`, etc.), the site has had Stripe re-added by mistake — see "What's deliberately not here" below.

## What's deliberately not here

Removed and should not return without explicit user request:

- **Stripe** — no `stripe`, `@stripe/stripe-js`, no `infra/api.ts` Lambda for webhooks, no `src/lib/stripe.ts`, no `src/app/api/checkout` or `/api/webhooks/stripe`.
- **Cart** — no `src/lib/cart.tsx`, no `<CartProvider>` in `layout.tsx`, no `/cart` page.
- **Checkout** — no `/checkout` route, no checkout success page, no `addToCart` UI.
- **Orders** — no DynamoDB `OrdersTable`, no `OrderProcessor` Lambda, no `@aws-sdk/client-dynamodb`/`-ses`/`-lib-dynamodb` deps, no `infra/database.ts`, no `infra/email.ts`.
- **Financing** — pricing FAQ does not advertise financing.

Product CTAs are "Request Info" → `/enterprise`. That's the entire commerce surface.

## Static binary assets (videos, large images)

`public/` files are committed to the repo (no LFS) and get uploaded to S3 by OpenNext on each deploy. Three gotchas:

- **Don't put static assets under a public/ subdir whose name matches an SSR route prefix.** OpenNext scans `public/` and adds each top-level subdir to the CloudFront Function's "send to S3" prefix list. If you put videos under `public/docs/`, *all* `/docs/*` requests (including the SSR pages at `src/app/docs/...`) get routed to S3 and return 403. Use a neutral prefix like `public/media/`. Current pattern: `/media/simulation/*.{mp4,jpg}` referenced from the `/docs/simulation` page.
- **MP4s for inline `<video>`**: re-mux with faststart so `moov` is at the front, otherwise the browser can't start playback until the full file downloads. ffmpeg one-liner (no re-encode):
  ```
  ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4
  ```
- **Posters**: extract a first-frame JPG and reference it via `poster="…"` on the `<video>` so the placeholder shows the scene rather than a black box.

## Common confusions

- **"The page is live but the videos 404"** → The Lambda was deployed (page.tsx) but the S3 upload was skipped or the assets weren't in `public/` at deploy time. Re-deploy from a working tree where the assets exist.
- **"GitHub shows a recent commit but prod still looks old"** → Nobody ran `sst deploy` after the push. Run it.
- **"Cursor's commit broke the deploy"** → Most likely Cursor re-added Stripe / cart / orders code that this site explicitly doesn't use. Strip it back out. See list above.
- **"`AssetsBucketBucket` lifecycle_rule.0.enabled is required"** → AWS provider tightened the schema. Each `lifecycleRules[]` entry needs `enabled: true` and an `id`. `infra/storage.ts` has the working shape — copy from there.
- **"coy.bot page returns 403 from `server: AmazonS3` but `coy.bot/...image.jpg` works"** → see the `public/` subdir gotcha above. Static files under `public/<route>/` shadow the SSR route `<route>`. Move them to `public/media/...` (or any subdir whose name isn't also a Next.js route).
- **"coy.bot SSL handshake fails (`*.cloudfront.net` cert returned)"** → `infra/web.ts` is missing the `domain` block, so SST stripped the alias and ACM cert from the CloudFront distribution on the last deploy. Re-add the `domain: { name: ..., redirects: [...] }` config and redeploy. (Status of the alias can be checked with `aws cloudfront get-distribution-config --id <prod-dist-id> --query 'DistributionConfig.Aliases'`.)

## Git identity

`~/.gitconfig` defaults to `<work-email>`, which is fine for other work but wrong for `coybot` org commits. This repo has a local override set at clone time:

```bash
git config user.name "yusuf-coybot"
git config user.email "218167113+yusuf-coybot@users.noreply.github.com"
```

If a fresh clone shows `Yusuf Saib <<work-email>>` in `git log -1`, the override is missing — set it before committing.

## End-to-end checklist for a typical change

1. Edit code in `www/`.
2. `npm run build` — must succeed.
3. `git add … && git commit` (yusuf-coybot identity) and `git push origin main`.
4. `AWS_PROFILE=coybot npx sst deploy --stage prod`.
5. Verify the change at https://coy.bot/<route> with `curl -I` or a browser. Static assets at https://coy.bot/<path>.
6. If something looks wrong, the page is cached at the CloudFront edge — wait a minute or hard-refresh.

## Local demo mode (offline)

The site supports a fully offline mode controlled by env vars — no code changes needed.

| | Local dev | AWS prod |
|---|---|---|
| Video | `/media/fleet-demo.mp4` (local file) | YouTube embed |
| AI planner | Ollama `llama3.2` at `localhost:11434` | Bedrock Claude Sonnet 4.6 |
| Rate limiting | Disabled (no `RATE_LIMIT_TABLE`) | DynamoDB 100 req/IP/hr |

### One-time setup (do while online)

```bash
# 1. Copy env template
cp .env.local.example .env.local

# 2. Install Ollama and pull the model
brew install ollama
ollama pull llama3.2

# 3. Place the demo video (too large for git — get from iCloud / team drive)
ffmpeg -i "/path/to/Fleet Demo.mp4" -c copy -movflags +faststart \
  public/media/fleet-demo.mp4
```

### Run offline

```bash
ollama serve &
npm run dev
# → http://localhost:3000, fully offline
```

### Why `NEXT_PUBLIC_OFFLINE_MODE` works this way

`NEXT_PUBLIC_*` vars are baked into the client bundle at `next build` time. `npm run dev` reads `.env.local` at startup, so the offline video/Ollama path is active locally. `sst deploy` runs on a machine without `.env.local`, so the YouTube/Bedrock path is active in production. No conditional build flags needed.

### `fleet-demo.mp4` is gitignored

The file is 169 MB — over GitHub's 100 MB per-file limit. It is listed in `.gitignore` and must be added manually per the steps above. If the video is missing, the offline video section will show a blank box (graceful degradation — no crash).
