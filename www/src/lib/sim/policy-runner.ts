// Thin wrapper around onnxruntime-web for the recurrent step-mode policies
// (quad: policy_v26rnn_dr.onnx, rover: policy_rover_v2.onnx). Both graphs
// share the same (state, h_in) -> (action, h_out) shape, so one class
// serves both — only stateDim/hiddenDim/actionDim differ, and those are
// read from the session's own metadata rather than hardcoded.
//
// onnxruntime-web is imported lazily (see loadOrt()) so it never lands in
// the homepage's initial JS bundle — only when the sim section is visible.

import type * as OrtNs from "onnxruntime-web";

type Ort = typeof OrtNs;

let ortPromise: Promise<Ort> | null = null;
let ortConfigured = false;

async function loadOrt(): Promise<Ort> {
  if (!ortPromise) {
    // "/wasm" subpath: wasm-only build (no WebGL/WebGPU EP glue — both models
    // use a GRU op that WebGPU's EP doesn't implement, so the wasm EP must be
    // the only one in play, not merely the preferred one).
    ortPromise = import("onnxruntime-web/wasm").then((ort) => {
      if (!ortConfigured) {
        ort.env.wasm.wasmPaths = "/ort/";
        ort.env.wasm.numThreads = 1;
        ortConfigured = true;
      }
      return ort;
    });
  }
  return ortPromise;
}

export interface PolicyRunnerOptions {
  /** external-data sidecar, e.g. rover's policy_rover_v2.onnx.data */
  externalDataUrl?: string;
  /** filename the graph references internally for its external data (see model.onnx strings) */
  externalDataName?: string;
  defaultHiddenDim?: number;
}

export class PolicyRunner {
  private session!: OrtNs.InferenceSession;
  private ort!: Ort;
  readonly stateDim: number;
  readonly hiddenDim: number;
  readonly actionDim: number;
  private h: Float32Array;

  private constructor(
    ort: Ort,
    session: OrtNs.InferenceSession,
    stateDim: number,
    hiddenDim: number,
    actionDim: number,
  ) {
    this.ort = ort;
    this.session = session;
    this.stateDim = stateDim;
    this.hiddenDim = hiddenDim;
    this.actionDim = actionDim;
    this.h = new Float32Array(hiddenDim);
  }

  static async load(
    modelUrl: string,
    opts: PolicyRunnerOptions = {},
  ): Promise<PolicyRunner> {
    const ort = await loadOrt();

    const sessionOpts: OrtNs.InferenceSession.SessionOptions = {
      executionProviders: ["wasm"],
    };
    if (opts.externalDataUrl) {
      sessionOpts.externalData = [
        { path: opts.externalDataName ?? opts.externalDataUrl, data: opts.externalDataUrl },
      ];
    }

    const session = await ort.InferenceSession.create(modelUrl, sessionOpts);

    const inputs = session.inputMetadata;
    const outputs = session.outputMetadata;
    const stateMeta = inputs.find((m) => m.name === "state");
    const hInMeta = inputs.find((m) => m.name === "h_in");
    const actionMeta = outputs.find((m) => m.name === "action");
    if (!stateMeta?.isTensor || !hInMeta?.isTensor || !actionMeta?.isTensor) {
      throw new Error(`unexpected policy graph shape for ${modelUrl}`);
    }

    const stateDim = Number(stateMeta.shape.at(-1));
    const hiddenDimRaw = hInMeta.shape.at(-1);
    const hiddenDim =
      typeof hiddenDimRaw === "number" ? hiddenDimRaw : (opts.defaultHiddenDim ?? 128);
    const actionDim = Number(actionMeta.shape.at(-1));

    if (!Number.isFinite(stateDim) || !Number.isFinite(actionDim)) {
      throw new Error(`could not resolve tensor dims for ${modelUrl}`);
    }

    return new PolicyRunner(ort, session, stateDim, hiddenDim, actionDim);
  }

  reset(): void {
    this.h = new Float32Array(this.hiddenDim);
  }

  /** state: length stateDim, raw SI units (no pre-normalization). Returns length actionDim. */
  async step(state: Float32Array): Promise<Float32Array> {
    if (state.length !== this.stateDim) {
      throw new Error(`state length ${state.length} != expected ${this.stateDim}`);
    }
    const { Tensor } = this.ort;
    const feeds: Record<string, OrtNs.Tensor> = {
      state: new Tensor("float32", state, [1, 1, this.stateDim]),
      h_in: new Tensor("float32", this.h, [1, 1, this.hiddenDim]),
    };
    const out = await this.session.run(feeds);
    const action = out.action.data as Float32Array;
    this.h = new Float32Array(out.h_out.data as Float32Array);
    return action;
  }
}
