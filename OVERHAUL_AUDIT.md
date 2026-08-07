# Presidio Overhaul — Phase 0 Audit (eco repo)

Scope: website (`www/`), benchmark reproducibility, dataset/model provenance, eco-side
licensing. SDK-specific findings live in `../sdk/OVERHAUL_AUDIT.md`. Audited by reading
this local checkout (`presidio-autonomy/eco`), not by crawling the live astral.us site — source is
ground truth here and this is a code-level audit.

Method note: every claim below is cited to a file:line or a live HF URL. Where I could not
verify something, it's marked **NEEDS-HUMAN** rather than guessed.

## Premises from the original brief that no longer hold

The brief that kicked off this overhaul assumed several problems that turned out to
already be fixed, or not to exist as described. Flagging these so no one re-does finished
work:

1. **"Stale footers (old M1-A/M1-G product names)"** — `src/components/layout/footer.tsx`
   already lists the current lineup (Quadcopter, Rover, Fixed-Wing, Phrover). The only
   surviving M1-A/M1-G reference anywhere in `www/` is `public/llms.txt:63` (see below).
2. **"`localhost:3000` og-image URL on /enterprise"** — not found. Root metadata
   (`src/app/layout.tsx:24-26`) uses `metadataBase: new URL(process.env.NEXT_PUBLIC_BASE_URL
   ?? "https://astral.us")`, and `/enterprise` resolves its OG URL from `SITE.origin`. The
   only `localhost` string in `www/src` is `src/app/api/plan-mission/route.ts:142`, a
   server-side Ollama fallback gated behind `OFFLINE_MODE==='true'` — intentional local-demo
   plumbing, not a leaked dev URL.
3. **"No team/about page"** — `/about` exists (`src/app/about/page.tsx`) with mission/pillars
   copy. What's actually missing is narrower: team bios and a funding/continuity story (see
   below).
4. **"Benchmark presented as footnote, not buying criterion"** — partially wrong. The
   homepage hero already leads with the benchmark (`src/components/sections/hero.tsx:67-79`)
   and there's a dedicated `/benchmark` page plus a "what to ask vendors" procurement angle
   in the blog (`src/app/blog/post-bodies.tsx:915-921`). The real problem here is different
   and arguably worse — see Risk #7 below.

## Website findings (`www/`)

- **Fixed-wing sensor claim is physically wrong and live today.** `src/lib/products.ts:125-143`
  gives the fixed-wing product the same "Intel RealSense D435" / "3D depth sensing" /
  "Real-time obstacle avoidance" spec as the quadcopter and rover, copy-pasted with no
  caveat, at a stated 45 MPH (≈20 m/s) cruise speed. A D435-class stereo camera's ~10 m
  usable range is traversed in ~0.5 s at that speed — this claim would fail basic technical
  diligence from any serious evaluator. **This is Risk #1.**
- **Benchmark numbers on the site are stale relative to Presidio's own later paper.**
  `src/app/benchmark/page.tsx:62-66` reports the modular pipeline at 9.98 m (worse than the
  9.50 m hover baseline in aggregate). But `src/app/blog/post-bodies.tsx:945,952` describes
  a later result where "six targeted fixes moved aggregate error from 9.98 m to 8.15 m,"
  beating hover for the first time — which matches `eco/papers/Engineering_the_Separation_Principle.md`.
  The benchmark page was never updated to reflect Presidio's own improved result, so the site
  currently understates its own capability and states a number the company has since beaten.
- **RESOLVED — "`ZeroClaw` dataset claimed public, not found published."** Confirmed with
  the author: ZeroClaw was the internal working name for what was later renamed and
  publicly released as Yonder. The `Yonder_NeurIPS_2026.md` paper itself corroborates this —
  it states that "32M bounding boxes / 4.4M annotated images / 405 categories" were the
  headline numbers "reported in earlier internal versions of this work" before
  license-incompatible scenes (ReplicaCAD/Replica/HM3D) were dropped down to the 167-scene
  public HSSD-only release — those exact figures match `Engineering_the_Separation_Principle`'s
  ZeroClaw table. `Engineering_the_Separation_Principle.md`/`.tex` and the corresponding blog
  post have been updated to say "Yonder" instead of "ZeroClaw," with a note at the dataset
  table clarifying that those specific numbers (275 scenes) reflect the pre-license-reduction
  internal snapshot used for those experiments, not the 167-scene public release. No change
  needed to `Closing_the_Metric_Gap.md`/`.tex` — grep found no ZeroClaw mentions there.
- **RESOLVED — `astralhf/astral-drone-models`.** Fetched the live HF card directly: it's a
  real, licensed (CC-BY-NC-4.0, commercial licensing via astral.us/enterprise) repo hosting
  six models (`yolov8n_domain_v3.onnx`, `vlm_lora_v1_q4km.gguf`+mmproj, `policy_v1.onnx`,
  `depth_v1.onnx`) that map directly to the "separation principle" architecture (VLM target
  selection + depth backprojection + reactive policy) from
  `Engineering_the_Separation_Principle.md`. It's a different lineage from the L5 nav
  policies in `astralhf/eco-drone-policies` (referenced by `setup_models.py`), not an
  orphaned/unaccounted repo — it's just not yet linked from the papers or website. Optional
  follow-up (not urgent): link it from the paper/`/research` page for discoverability.
- Enterprise page claim **"manage hundreds of drones from a single dashboard"**
  (`src/app/enterprise/page.tsx:22`) has no supporting benchmark or paper result — this is
  exactly the "claimed vs. demonstrated operator-ratio gap" the master brief's Phase 3 wants
  the multi-vehicle RFC to address, except right now the unsupported version of the claim is
  already live on the enterprise page.
- Obstacle-avoidance/depth-sensing claims across all three vehicle product pages
  (`src/lib/products.ts:41,84,128`) are backed by real results, but the papers' own stated
  limitations ("all results are simulation-only; real-world transfer unvalidated," "indoor
  environments only" — `Engineering_the_Separation_Principle.md:332`) aren't carried onto the
  product pages. This is the "maturity label" gap the overhaul brief anticipated for Phase 5
  — it applies to Phase 2/5 product copy generally, not just fixed-wing.
- Minor: `/docs` page has `title`/`description` but no OG/Twitter card metadata
  (`src/app/docs/page.tsx:9-13`) — falls back to generic root OG image. Cosmetic.
- No dead internal links found in the sampled nav/footer/page set.
- `/company`, `/continuity`, and `/licensing` pages do not exist yet (confirmed absent from
  `src/app/*`). `/about` has mission copy but no team bios and no continuity/funding
  paragraph — both genuinely need **NEEDS-HUMAN** input (real names/bios, real
  funding/continuity facts), not drafted copy.

## Benchmark reproducibility — the big finding

The actual scoring code that produces the 9.50 m / 9.98 m / 1.04 m numbers **does not exist
in this repo.**

- What's in `eco/drone/sim/` (`scorecard.py`, `scenario.py`, `team_world.py`,
  `team_runtime.py`) is a *different* benchmark: a multi-agent (quad+rover) intervention/
  autonomy-level scorecard. No hover baseline, no VLM concepts, no aggregate mean position
  error anywhere in it.
- `eco/drone/sim/BRINGUP.md:11,21` and `sim_bridge.py:35` point to the real harness living on
  a remote GPU host ("hoopoe"), at `~/code/ishmael/swarm_eval/harness/isaac_sim_bridge.py`,
  referenced only via an `ISHMAEL_HARNESS` env var — it isn't checked into this repo.
- The paper's own code release says so directly: `eco/papers/yonder_code.zip` →
  `yonder-code-release/README.md` states *"the full closed-loop benchmark runner ... are
  described in the paper but are not anonymized for double-blind release here."*
- Reference [18] in the Yonder paper (the thing being benchmarked against) is confirmed to
  be an anonymized self-citation to Presidio's own `Closing the Metric Gap` paper — i.e. Presidio
  *is* the benchmark's author, it's just not in a public repo.
- Static check on what code does exist: `reproduce_finding.py` inside the paper's code zip
  computes aggregate error as a plain mean over trial logs, and one logged condition has
  exactly 153 trials, consistent with the papers' "51 tasks × 3 envs × 1 seed" math. That's
  the extent of what's independently checkable from this repo — it validates arithmetic on
  already-recorded logs, not that the simulation itself produced correct numbers.
- No tier/version scaffolding and no fault-injection code exist for this specific benchmark.
  (Fault injection *does* exist, but only in the unrelated multi-agent scorecard —
  `drone/sim/scenario.py:22-25`, `:274`, `:290`, `:295`.)

**Verdict: not verifiable in this environment** (as originally audited). **Update**: the
harness has since been moved from the untracked hoopoe working directory into
[presidio-autonomy/benchmark](https://github.com/presidio-autonomy/benchmark) (private repo). This resolves
the "the code doesn't exist anywhere version-controlled" problem, but **the repo is
private** — Phase 3's "the harness is open, bring your vendor's stack" pitch still cannot
ship honestly until someone makes the actual release decision (public visibility, license,
removing/handling anything not meant for outside eyes). That decision — not the git
migration — is what should gate Phase 3 copy and `--tier outdoor|fleet` scaffolding.

## Dataset / model provenance

- **Yonder**: real, confirmed via live HF card. Data is CC-BY-NC-4.0 (inherits HSSD's
  NonCommercial restriction, attribution preserved), code is Apache-2.0. Card documents
  intended use, limitations, and provenance reasonably well; it has a "Not for" block listing
  commercial use as prohibited but no dedicated "Commercial use" section pointing to an
  alternative — that's the real gap for Phase 1, not "provenance is unstated" as the brief
  assumed.
- **Shipped nav policies** (`eco/drone/models/policy_*.onnx`): none of the four
  `*_train_summary.json`/`*_rl_summary.json` files contain a dataset-name or license field.
  Best-effort reading of their contents (PPO hyperparameters, reward-shaping terms, no
  dataset path) indicates these are pure sim-RL/behavioral-cloning, **not** Yonder-derived —
  so the CC-BY-NC-4.0 restriction likely doesn't attach to them. But this is inferred, not
  stated anywhere, which is itself the gap: an outside evaluator has no way to reach the same
  conclusion without doing what we just did.
- `eco/drone/models/setup_models.py` pulls the L5 nav policies from
  `astralhf/eco-drone-policies` (public HF repo) and pulls other artifacts
  (`vlm_drone`, `depth_model`, `domain_detector`) from S3 with inline docstring notes on
  training data (e.g. "2,000 VisDrone aerial scenes") — but no license field anywhere in
  code or `README.md` for any of these.
- `eco/NOTICE` already discloses third-party licenses well for code-adjacent components:
  YOLOv8 (AGPL-3.0, "model weights only, not linked"), Phi-3 (MIT), Llama 3 (Meta Community
  License), ArduPilot MAVLink (LGPL-3.0). Good existing practice — it just doesn't yet cover
  Yonder/HSSD attribution or the HF-hosted model weights, which fall outside the repo's
  Apache-2.0 code license and currently have no license statement of their own.

## Ranked risk list

1. **HIGH** — Fixed-wing product page claims D435 stereo obstacle avoidance at 45 MPH
   cruise; physically incorrect and live on a marketing page right now.
2. **MEDIUM** (downgraded from HIGH) — Benchmark harness is now version-controlled at
   [presidio-autonomy/benchmark](https://github.com/presidio-autonomy/benchmark) but still **private**;
   Phase 3's "the harness is open" pitch remains false as written until a public-release
   decision is made.
3. ~~MEDIUM — `ZeroClaw` dataset described as "publicly available"~~ **RESOLVED**: ZeroClaw
   was Yonder's internal codename; papers/blog renamed accordingly, see above.
4. **MEDIUM** — Model weight provenance/license undocumented (no data-source/license field
   in any train/RL summary; NOTICE doesn't cover Yonder attribution or HF-hosted weights).
5. ~~MEDIUM — `astralhf/astral-drone-models` unaccounted-for~~ **RESOLVED**: confirmed real,
   licensed, and tied to the separation-principle model lineage, see above.
6. **LOW** — Benchmark page (9.98 m) is stale versus Presidio's own later, better result
   (8.15 m) reported in a blog post/paper — site understates the company's own progress.
7. **LOW** — Enterprise page's "hundreds of drones, one dashboard" claim is unsupported by
   any benchmark or paper result.
8. **LOW** — `public/llms.txt` still names M1-A/M1-G.
9. **LOW** — No `/company`, `/continuity`, `/licensing` pages yet; `/about` lacks team bios
   and a continuity/funding paragraph (both need real human-provided facts).
10. **LOW** — `/docs` page missing OG/social metadata (cosmetic).

## What this blocks in later phases

- **Phase 3** (benchmark-as-product) cannot honestly ship the "open harness, bring your
  stack" pitch until a decision is made about publishing the actual harness code (currently
  on `hoopoe`, outside any repo). Needs human/eng-lead input before any Phase 3 copy work.
- **Phase 5** (fixed-wing sensor story) is well-scoped and low-risk to execute — the pod
  reference-design doc and the `products.ts`/fixed-wing page fix are independent of the
  benchmark-harness blocker.
- **Phase 1** (licensing) can proceed largely as planned; the Yonder side is in better shape
  than assumed, but the model-weight provenance gap (Risk #4) and the orphaned
  `astral-drone-models` repo (Risk #5) need resolving first or the provenance table will have
  more "unknown" rows than the brief anticipated.

## Human-approval gates (tracked here per master-prompt instructions)

- [ ] Counsel sign-off: Yonder commercial license text
- [ ] Counsel sign-off: provenance statements + `/licensing` page
- [ ] Founder input: team bios, funding/continuity paragraph
- [ ] Eng lead sign-off: MissionPlan v2 schema (API stability commitment) — see `sdk/OVERHAUL_AUDIT.md`
- [ ] Eng lead sign-off: benchmark v2 RFCs before any public leaderboard changes — **blocked
      until the harness-publication question above is resolved**
- [ ] Real flight footage for demos 1–4 hardware segments
- [ ] Marketing review: all rewritten copy vs. maturity labels
- [x] ~~NEEDS-HUMAN: confirm ZeroClaw's actual publication status~~ resolved — it's Yonder's
      old internal name, papers/blog renamed
- [x] ~~NEEDS-HUMAN: confirm ownership/contents/license of `astralhf/astral-drone-models`~~
      resolved — real, licensed, tied to the separation-principle models
- [x] Benchmark harness moved into version control: private
      [presidio-autonomy/benchmark](https://github.com/presidio-autonomy/benchmark), migrated from
      `~/code/ishmael/benchmark/` on hoopoe (excludes `results/`, model checkpoints, demo
      videos — see its README/.gitignore). This unblocks Phase 3 mechanically, but the repo
      is **private** — Phase 3 still cannot claim "the harness is open" until a decision is
      made to make it public. That's a release decision, not a git-hygiene step.
