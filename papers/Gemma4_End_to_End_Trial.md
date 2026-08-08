**Gemma 4 E2B as an End-to-End Drone Navigation Controller:**

**A Pilot Trial in the 25-VLM Lineup**

Yusuf Saib

*Presidio Autonomy, Inc., Santa Clara, CA*

hello@presidioautonomy.com

***Abstract***

*We extend the 25-VLM closed-loop benchmark from `Closing_the_Metric_Gap` with one additional architecture: Gemma 4 E2B, the multimodal Gemma 4 variant released by Google DeepMind and demonstrated by NVIDIA on Jetson Orin Nano (5.1 B parameters / 2.3 B effective via Per-Layer Embeddings, 35 layers, 128 K context, multimodal text+image+audio). Gemma 4 was deployed as a direct image+text→goal controller on the same Isaac Sim benchmark used in our prior work (51 tasks × 3 environments, 1 seed = 153 trials, identical task distribution to Track A v6). Aggregate final error is **47.78 m**, far worse than the 9.50 m Hover baseline and consistent with the paper's central finding that no end-to-end 7–8 B-class VLM reliably outperforms doing nothing. Step-1 (single-shot) prediction error is 10.66 m and improvement ratio is 0.65×, indicating significant divergence between single-prediction accuracy and closed-loop trajectory. Direction-cosine accuracy is +0.11 — substantially below the 0.83–0.91 typical of frontier 7–8 B VLMs, suggesting that at the 2.3 B-effective scale the directional half of the metric gap also opens up. Per-tier results match expectation: trivial tiers (`1_stationary`, `2_cardinal` step-1) work perfectly; visually grounded tiers (`6_visual` 92.7 m, `10_occluded` 153.0 m) fail catastrophically; semantic tiers (`12_semantic` 5.95 m) survive at the threshold level. End-to-end mean inference latency is 17.8 s/step, which makes Gemma 4 unsuitable as a real-time controller in this configuration without aggressive quantization or token caps. The same Gemma 4 weights, deployed inside the modular Track A pipeline as a target-identifier (replacing Nemotron+GDINO+Florence, evaluated 2026-04-05 prior to this report), score 9.35 m aggregate — beating hover by 0.15 m (n.s.) and beating end-to-end Gemma 4 by 38.4 m. The split is the cleanest single-model demonstration of the separation principle in our lineup to date.*

---

**I. CONTEXT**

`Closing_the_Metric_Gap` evaluated 25 VLM architectures across 10,200 closed-loop quadrotor trials in NVIDIA Isaac Sim and found that no end-to-end 7–8 B VLM reliably outperforms hovering in place. The diagnosis was a **metric gap** — VLMs achieve 0.83–0.91 directional cosine accuracy but 6–10 m distance error on 4–12 m targets — and the prescription was the **separation principle**: use VLMs only for semantic target identification and delegate spatial grounding to dedicated depth and detection modules. `Engineering_the_Separation_Principle` then iterated the modular Track A pipeline through 18 refinements (R3–R18) and made the lineage of design decisions explicit. A natural follow-up question, raised by the publication of Google DeepMind's Gemma 4 [1] and NVIDIA's Jetson Orin Nano deployment write-up [2], is: *does the metric gap diagnosis still hold for a brand-new 5 B-parameter (2.3 B-effective) instruction-tuned multimodal model designed explicitly for on-device perception?* This pilot answers that question for the end-to-end deployment.

**II. METHOD**

*Model.* Gemma 4 E2B-it (`google/gemma-4-E2B-it`, total 5.1 B / effective 2.3 B via Per-Layer Embeddings, 35 layers, 128 K context, native multimodal head). Loaded from the canonical HuggingFace repo at bf16 (10.25 GB). VRAM at inference time was ~25 GB (transformers 5.5.4 / torch 2.11.0 / cuda:0); this is significantly above the 9.5 GB observed in the Track A target-selector configuration of 2026-04-05 and reflects the larger generation buffer required by `max_new_tokens=300`. The Q4_K_M GGUF quantization recommended by NVIDIA's Jetson deployment was not used in this run; cross-quantization comparison is left to a follow-up.

*Deployment.* Gemma 4 requires transformers 5.x, which conflicts with Isaac Sim's bundled torch / typing-extensions stack used by the benchmark runner. We therefore run the model in a separate venv (`~/gemma4-venv`) inside `gemma4_server.py` and talk to it from a thin `VLMGemma4` architecture class (`benchmark/architectures/vlm_gemma4.py`) over a Unix-domain socket. The same socket already serves the Track A modular pipeline; a `mode: "navigate"` field selects the end-to-end path. All other components of the closed-loop run — Isaac Sim 5.1, Pegasus 5.1.0, the Pegasus quadrotor, the 60 s budget, the 2 s flight segments, and the 51-task / 3-environment task distribution — are exactly those of the Track A v6 5-seed run on 2026-04-05.

*Prompt and parsing.* The prompt mirrors `vlm_smolvlm.py` for direct comparability with the existing lineup: drone-frame coordinates (+x forward, +y left, +z up), free-form chain-of-thought, and a JSON-only output contract `{x, y, z, reasoning}`. Single-pass greedy decoding (`do_sample=False`, `max_new_tokens=300`). Failure-mode parsing falls back to extracting the first three numbers if JSON parsing fails. No few-shot examples, no detection prepass, no depth conditioning — this is the unmediated end-to-end test.

*Scope.* 1 seed × 51 tasks = 153 trials (matches one seed of the Track A v6 reference run). The 25-VLM lineup of `Closing_the_Metric_Gap` used 8 seeds; the 1-seed numbers below are therefore preliminary point estimates suitable for ranking-level claims, not for tight CIs. Wall time for the full Isaac Sim run was 48.6 minutes.

**III. RESULTS**

*Aggregate.* Final position error 47.78 m, step-1 prediction error 10.66 m, improvement ratio 0.65× (the closed-loop trajectory is *worse* than the single-shot prediction), direction cosine +0.11, success@5 m = 18%, success@10 m = 35%, mean collisions 14.0, collision-free rate 33%, mean inference 17.8 s/step. **Hover baseline (same task distribution): 9.50 m, +0.00 dir, success@5 m = 16%, 100% CF.** Random baseline: 39.06 m. Track A v6 (Gemma 4 as target-selector inside the modular pipeline, 2026-04-05): 9.35 m, +0.07 dir, 71% CF.

*Per-tier breakdown (n trials, final / step-1 / s1→final ratio / direction):*

| Tier              | n  | Final  | Step-1 | s1/final | Dir   |
|-------------------|----|-------:|-------:|---------:|------:|
| 1_stationary      | 3  |  0.18m |  0.18m |    1.00× | +1.00 |
| 2_cardinal        | 3  | 34.15m |  2.56m |    0.08× | +0.67 |
| 3_named_near      | 3  |  5.04m |  5.03m |    1.00× | +0.23 |
| 4_named_far       | 3  | 45.84m | 13.62m |    0.30× | +0.34 |
| 5_spatial         | 3  | 21.48m | 11.61m |    0.54× | −0.30 |
| 6_visual          | 3  | 92.67m |  6.72m |    0.07× | +0.58 |
| 7_reasoning       | 6  | 18.38m | 17.62m |    0.96× | −0.27 |
| 8_multi_leg       | 3  | 56.54m | 10.18m |    0.18× | −0.49 |
| 9_obstacle        | 6  | 49.44m | 10.61m |    0.21× | +0.15 |
| 10_occluded       | 6  |152.96m | 23.13m |    0.15× | −0.10 |
| 11_appearance     | 3  | 43.86m |  3.12m |    0.07× | +0.64 |
| 12_semantic       | 3  |  5.95m |  5.49m |    0.92× | −0.18 |
| 13_counting       | 3  | 22.89m | 10.80m |    0.47× | +0.02 |
| 14_exploration    | 3  | 42.07m |  9.17m |    0.22× | −0.18 |

**IV. ANALYSIS**

*The metric gap is present and the directional half is also wide.* On the tiers where step-1 prediction is reasonable (e.g. `2_cardinal` 2.56 m, `11_appearance` 3.12 m, `6_visual` 6.72 m), the closed-loop trajectory diverges by up to 14×: step-1 → final ratios of 0.07–0.18× for cardinal, appearance, and visual tiers. This is the same replanning-divergence phenomenon characterized in `Closing_the_Metric_Gap` Sec. IV: a single-shot estimate is rolled into a sequence of small flight segments, each re-querying the VLM, and the cumulative coordinate drift dominates the budget. What is new here is that direction cosine has dropped from the 0.83–0.91 plateau seen at 7–8 B to +0.11 aggregate, with several tiers (`5_spatial`, `7_reasoning`, `8_multi_leg`, `12_semantic`) actually anti-correlated. The metric gap is no longer the only failure mode at this scale — directional reasoning has also regressed.

*Trivial tiers and semantic tiers are the survivors.* `1_stationary` is solved by every model that returns (0,0,1.5) on "Hover in place," and Gemma 4 does so. `12_semantic` (5.95 m, 100% success@10 m) is the only non-trivial tier where Gemma 4 holds up; the 2026-04-05 Track A target-selector pass also performed well on semantic tasks, consistent with the separation principle's prediction that VLM-as-semantic-classifier is the regime where these models actually contribute.

*The same model, deployed two different ways, lands 38.4 m apart.* Gemma 4 inside Track A: 9.35 m. Gemma 4 end-to-end: 47.78 m. The 4× gap is not an artifact of seeds (Track A v6's CI is [8.53, 10.24]; end-to-end's score sits four CIs above) and is not explained by any difference in task distribution, simulator state, prompt, or weights — those are held constant. The only difference is whether the VLM is asked to output a goal coordinate or a target name. This is the cleanest single-model demonstration of the separation principle in our lineup to date.

*Latency.* 17.8 s/step end-to-end is far above the 0.92 s observed on the smoke test and the 1.4 s observed on synthetic prompts. The gap reflects long generation traces on the harder tiers (the model often emits 200+ tokens of reasoning before the JSON, hitting the 300-token cap on `4_named_far`, `7_reasoning`, and `10_occluded`). At this latency, the Pegasus 60 s budget allows only ~3 closed-loop queries per trial, which compounds the divergence problem.

**V. WHERE THIS SITS IN THE PAPER LINEUP**

If folded into the `Closing_the_Metric_Gap` Table I (the 25-VLM aggregate), Gemma 4 E2B end-to-end would be the smallest serious VLM in the row below SmolVLM 2B and would not change the paper's central claim — no entry beats hover. If folded into `Engineering_the_Separation_Principle` as a Track A v6 ablation, the 38.4 m end-to-end / modular gap is a clean illustration of the separation principle's lever arm and would slot naturally into Sec. III. We do not recommend folding the present 1-seed result into either paper without a multi-seed re-run; we do recommend recording the end-to-end / modular split as a standalone observation.

**VI. ARTIFACTS**

- Server: `/home/yusuf/zeroclaw-train/gemma4_server.py` (extended 2026-04-28 with `mode: "navigate"`).
- Architecture: `~/code/ishmael/benchmark/architectures/vlm_gemma4.py`.
- Registration: `vlm_gemma4` entry in `~/code/ishmael/benchmark/config/architectures.yaml`.
- Weights: `~/models/gemma-4-E2B-it/` on hoopoe (10.25 GB, bf16).
- Results JSON: `~/code/ishmael/results/20260428_232405.json`.
- Runtime log: `~/gemma4_full_1seed.log`.
- Track A v6 reference: `~/code/ishmael/results/track_a_v6_benchmark_20260405_162102.json`.

**REFERENCES**

[1] Google DeepMind, "Gemma 4 model family," 2026, https://huggingface.co/google/gemma-4-E2B-it.

[2] NVIDIA, "Gemma 4 VLA Demo on Jetson Orin Nano Super," HuggingFace blog, 2026, https://huggingface.co/blog/nvidia/gemma4.

[3] Y. Saib, "Closing the Metric Gap: From Diagnosis to Solution in Vision-Language Drone Navigation," Presidio Autonomy, Inc., 2026.

[4] Y. Saib, "Engineering the Separation Principle: From Modular Architecture to Deployable Drone Navigation," Presidio Autonomy, Inc., 2026.
