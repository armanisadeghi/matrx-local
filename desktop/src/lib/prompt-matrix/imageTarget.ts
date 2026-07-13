/**
 * The IMAGE target — the first consumer of the matrix engine.
 *
 * Declares which image-generation parameters can be swept as a variable and
 * how each one lands on an ImageGenerateInput. Every constraint here mirrors
 * the engine's own validation (app/api/image_gen_routes.py → GenerateRequest),
 * so a bad option value is caught in the UI at plan time instead of 400-ing
 * halfway through a 120-image overnight run.
 */

import type { ImageGenerateInput } from "@/hooks/use-media-gen";
import type { ImageGenLoraInfo, ImageGenModelInfo } from "@/lib/api";
import type { MatrixTarget, ParamAxis, ParseResult } from "./targets";
import { MAX_SEED } from "./rng";
import type { TemplateField } from "./types";

type Job = ImageGenerateInput;

const ok = (value: unknown): ParseResult => ({ ok: true, value });
const err = (error: string): ParseResult => ({ ok: false, error });

/** Integer parser with engine-matching bounds. */
function intAxis(
  min: number,
  max: number,
  opts?: { multipleOf?: number },
): (raw: string) => ParseResult {
  return (raw: string) => {
    const text = raw.trim();
    if (text === "") return err("cannot be empty");
    const n = Number(text);
    if (!Number.isFinite(n) || !Number.isInteger(n)) {
      return err("must be a whole number");
    }
    if (n < min || n > max) return err(`must be between ${min} and ${max}`);
    const mult = opts?.multipleOf;
    if (mult !== undefined && n % mult !== 0) {
      return err(`must be a multiple of ${mult}`);
    }
    return ok(n);
  };
}

function floatAxis(min: number, max: number): (raw: string) => ParseResult {
  return (raw: string) => {
    const text = raw.trim();
    if (text === "") return err("cannot be empty");
    const n = Number(text);
    if (!Number.isFinite(n)) return err("must be a number");
    if (n < min || n > max) return err(`must be between ${min} and ${max}`);
    return ok(n);
  };
}

/** `1024x1024`, `1024 × 768`, `832*1216` — all accepted. */
function parseSize(raw: string): ParseResult {
  const m = /^\s*(\d+)\s*[x×*]\s*(\d+)\s*$/i.exec(raw);
  if (m === null) return err("must look like 1024x1024");
  const width = Number(m[1]);
  const height = Number(m[2]);
  for (const [name, n] of [
    ["width", width],
    ["height", height],
  ] as const) {
    if (n < 64 || n > 2048) return err(`${name} must be between 64 and 2048`);
    if (n % 8 !== 0) return err(`${name} must be a multiple of 8`);
  }
  return ok({ width, height });
}

/** Advanced pipeline kwarg: `extra:<key>` — value is JSON, else a raw string. */
function extraParamAxis(key: string): ParamAxis<Job> {
  return {
    id: `extra:${key}`,
    label: key,
    group: "Advanced",
    hint: `Advanced pipeline parameter "${key}" (JSON values allowed).`,
    parse: (raw) => {
      const text = raw.trim();
      if (text === "") return err("cannot be empty");
      try {
        return ok(JSON.parse(text) as unknown);
      } catch {
        return ok(text); // a plain string is a legitimate value
      }
    },
    apply: (job, value) => ({
      ...job,
      extra_params: { ...(job.extra_params ?? {}), [key]: value },
    }),
  };
}

export interface ImageTargetContext {
  /** Downloaded models — a `model` sweep may only name models that can run. */
  models: readonly ImageGenModelInfo[];
  /** INSTALLED LoRAs — a `lora` sweep may only name ids that exist on disk. */
  loras: readonly ImageGenLoraInfo[];
}

export const IMAGE_TEMPLATE_FIELDS: TemplateField[] = [
  { id: "prompt", label: "Prompt", text: "" },
  { id: "negative_prompt", label: "Negative prompt", text: "" },
];

export function createImageTarget(ctx: ImageTargetContext): MatrixTarget<Job> {
  const downloaded = ctx.models.filter((m) => m.is_downloaded);

  const axes: ParamAxis<Job>[] = [
    {
      id: "steps",
      label: "Steps",
      group: "Sampling",
      hint: "Denoising steps. More is usually slower, not always better.",
      suggestions: ["4", "8", "20", "30", "50"],
      parse: intAxis(1, 150),
      apply: (job, value) => ({ ...job, steps: value as number }),
    },
    {
      id: "guidance",
      label: "Guidance (CFG)",
      group: "Sampling",
      hint: "How hard the model is pushed toward the prompt.",
      suggestions: ["1", "3.5", "7", "9", "12"],
      parse: floatAxis(0, 20),
      apply: (job, value) => ({ ...job, guidance: value as number }),
    },
    {
      id: "seed",
      label: "Seed",
      group: "Sampling",
      hint: "Sweeping the seed overrides the batch seed policy.",
      parse: intAxis(0, MAX_SEED),
      apply: (job, value) => ({ ...job, seed: value as number }),
    },
    {
      id: "size",
      label: "Size (W×H)",
      group: "Image",
      hint: "Both dimensions at once — multiples of 8, 64–2048.",
      suggestions: ["1024x1024", "832x1216", "1216x832", "768x768", "512x512"],
      parse: parseSize,
      apply: (job, value) => {
        const { width, height } = value as { width: number; height: number };
        return { ...job, width, height };
      },
    },
    {
      id: "width",
      label: "Width",
      group: "Image",
      parse: intAxis(64, 2048, { multipleOf: 8 }),
      apply: (job, value) => ({ ...job, width: value as number }),
    },
    {
      id: "height",
      label: "Height",
      group: "Image",
      parse: intAxis(64, 2048, { multipleOf: 8 }),
      apply: (job, value) => ({ ...job, height: value as number }),
    },
    {
      id: "model",
      label: "Model",
      group: "Model",
      hint: "Compare models on the same prompt. Only downloaded models can run.",
      suggestions: downloaded.map((m) => m.model_id),
      parse: (raw) => {
        const id = raw.trim();
        if (id === "") return err("cannot be empty");
        const model = ctx.models.find((m) => m.model_id === id);
        if (model === undefined) {
          return err(
            `unknown model. Available: ${downloaded.map((m) => m.model_id).join(", ") || "none downloaded"}`,
          );
        }
        if (!model.is_downloaded) {
          return err("is not downloaded — download it before sweeping it");
        }
        return ok(id);
      },
      apply: (job, value) => ({ ...job, model_id: value as string }),
    },
    {
      id: "lora",
      label: "LoRA",
      group: "Model",
      hint: 'Installed LoRA id, optionally "id@scale" (e.g. my-lora@0.8). Empty = no LoRA.',
      suggestions: ["", ...ctx.loras.map((l) => l.id)],
      parse: (raw) => {
        const text = raw.trim();
        if (text === "") return ok([]); // "no LoRA" is a legitimate arm
        const [id = "", scaleText] = text.split("@");
        const loraId = id.trim();
        if (!ctx.loras.some((l) => l.id === loraId)) {
          return err(
            `unknown LoRA. Installed: ${ctx.loras.map((l) => l.id).join(", ") || "none"}`,
          );
        }
        let scale = 1;
        if (scaleText !== undefined) {
          scale = Number(scaleText.trim());
          if (!Number.isFinite(scale) || scale < 0 || scale > 2) {
            return err("scale must be a number between 0 and 2");
          }
        }
        return ok([{ id: loraId, scale }]);
      },
      apply: (job, value) => {
        const loras = value as { id: string; scale: number }[];
        if (loras.length === 0) {
          const { loras: _dropped, ...rest } = job;
          return rest;
        }
        return { ...job, loras };
      },
    },
    {
      id: "lora_scale",
      label: "LoRA scale",
      group: "Model",
      hint: "Sweep the strength of the LoRA(s) already selected in the form.",
      suggestions: ["0.25", "0.5", "0.75", "1"],
      parse: floatAxis(0, 2),
      apply: (job, value) => {
        const scale = value as number;
        const current = job.loras ?? [];
        if (current.length === 0) return job;
        return { ...job, loras: current.map((l) => ({ ...l, scale })) };
      },
    },
    {
      id: "strength",
      label: "img2img strength",
      group: "Image",
      hint: "0 = keep the input image, 1 = ignore it. Requires an input image.",
      suggestions: ["0.3", "0.5", "0.7", "0.9"],
      parse: floatAxis(0, 1),
      apply: (job, value) => ({ ...job, strength: value as number }),
    },
  ];

  const byId = new Map(axes.map((a) => [a.id, a]));

  return {
    id: "image",
    label: "Image",
    fields: IMAGE_TEMPLATE_FIELDS,
    axes,
    resolveAxis: (axisId) => {
      const known = byId.get(axisId);
      if (known !== undefined) return known;
      if (axisId.startsWith("extra:")) {
        const key = axisId.slice("extra:".length);
        return key.length > 0 ? extraParamAxis(key) : null;
      }
      return null;
    },
    applyField: (job, fieldId, text) => {
      if (fieldId === "prompt") return { ...job, prompt: text };
      if (fieldId === "negative_prompt") {
        if (text.trim() === "") {
          const { negative_prompt: _dropped, ...rest } = job;
          return rest;
        }
        return { ...job, negative_prompt: text };
      }
      return job;
    },
    applySeed: (job, seed) => ({ ...job, seed }),
  };
}
