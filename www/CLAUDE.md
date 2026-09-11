# presidioautonomy.com — deploy + git workflow

This is the marketing/education site at https://presidioautonomy.com. It is **informational
only**: no cart, no checkout, no payment processing, no financing, no orders flow, no operator
console. Any agent that wants to "add" any of those should stop and ask the user first.

## What lives where

| Concern | Location |
|---|---|
| Source code | `presidio-autonomy/eco` GitHub repo, this directory (`www/`) |
| Production site | AWS, deployed via SST → CloudFront + Lambda + S3 |
| Stages | `dev` (https://dev.presidioautonomy.com) and `prod` (https://presidioautonomy.com) — these two literal stage names, nowhere else |
| AWS profile | `presidio` (same account as `astral`, `~/.aws/config` + credentials) |
| Git identity | `ys1382` (see the superproject's `GIT_IDENTITY.md`) |

## The single most important rule

**There is no CI deploy.** A `git push` updates only the repo. AWS is updated only when someone
runs `just deploy-dev` / `just deploy-prod` (or the equivalent `npx sst deploy --stage <stage>`)
from a working tree. `main` and what's actually live can drift — that's expected, not a bug.

## Deploy

From `www/`:

```bash
just deploy-dev    # https://dev.presidioautonomy.com
just deploy-prod   # https://presidioautonomy.com
```

**Prerequisite, checked once:** the Route53 hosted zone for presidioautonomy.com must already
exist and be delegated from GoDaddy (see `../scripts/create-hosted-zone.sh` and
`dig NS presidioautonomy.com @1.1.1.1`). Deploying before delegation propagates hangs ACM
certificate validation.

The deploy:
1. Runs `next build` (OpenNext picks up the result).
2. Uploads everything in `public/` to the AssetsBucket (S3), served via CloudFront.
3. Updates the Next.js Lambda with the SSR/route handlers.
4. Invalidates CloudFront paths that changed.

Typical run: 4–8 min on `dev`, longer on the very first `prod` deploy (new CloudFront
distribution, ~15–20 min). Watch for Pulumi errors near the end — exit code 0 does not always
mean success; scan the tail for `Error` or `Failed`.

## Static binary assets (images, video)

`public/` files are committed to the repo (no LFS) and get uploaded to S3 by OpenNext on each
deploy. Two gotchas, both bitten before on the old astral.us site:

- **Never put static assets under a `public/<dir>` whose name matches a route.** OpenNext scans
  `public/` and routes each top-level subdir straight to S3. A `public/blog/` or
  `public/autonomy/` directory would silently 403 every request to the real `/blog` or
  `/autonomy` SSR route. Everything static goes under `public/media/`, no exceptions.
- **MP4s for inline `<video>`** need `-movflags +faststart` (moves `moov` to the front) or the
  browser can't start playback until the whole file downloads:
  ```
  ffmpeg -i in.mp4 -c copy -movflags +faststart out.mp4
  ```

## Common confusions

- **"The domain doesn't have a valid cert / handshake returns a `*.cloudfront.net` cert"** →
  `infra/web.ts` lost its `domain: { name, redirects }` block on a deploy. SST strips the
  CloudFront alias + ACM cert when that block is missing. Re-add it and redeploy.
- **"`sst deploy` from `eco/aws` doesn't seem to do anything"** → that's a different app
  (`presidio-drone-api`) and a different, documented footgun — see the root `eco/CLAUDE.md`.
  Never confuse the two; this file is only about `www/`.

## Git identity

The global `~/.gitconfig` default identity is wrong for `presidio-autonomy` commits. Check
`git log -1` before committing — it should show `ys1382`. If it shows anything else, this
clone is missing its repo-local override.

## End-to-end checklist for a typical change

1. Edit code in `www/`.
2. `npm run build` — must succeed locally.
3. `git add … && git commit` (`ys1382` identity) and `git push origin main`.
4. `just deploy-dev`, verify at https://dev.presidioautonomy.com.
5. `just deploy-prod`, verify at https://presidioautonomy.com.
6. If something looks wrong right after deploy, it may be cached at the CloudFront edge — wait
   a minute or hard-refresh before assuming the deploy failed.
