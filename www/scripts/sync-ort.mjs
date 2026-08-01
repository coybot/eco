// Copies the onnxruntime-web wasm runtime into public/ort/ so it's served
// from our own origin instead of a CDN. Re-run after bumping onnxruntime-web.
import { copyFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const srcDir = join(root, "node_modules/onnxruntime-web/dist");
const destDir = join(root, "public/ort");

const files = [
  "ort-wasm-simd-threaded.wasm",
  "ort-wasm-simd-threaded.mjs",
  // .bundle.min.mjs is what "onnxruntime-web/wasm" resolves to by default
  // (the non-bundle ort.wasm.min.mjs is only used under a bundler-specific
  // "onnxruntime-web-use-extern-wasm" condition Next.js doesn't set).
  "ort.wasm.bundle.min.mjs",
];

mkdirSync(destDir, { recursive: true });
for (const f of files) {
  copyFileSync(join(srcDir, f), join(destDir, f));
  console.log(`synced ${f}`);
}
