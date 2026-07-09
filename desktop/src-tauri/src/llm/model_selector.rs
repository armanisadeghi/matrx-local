use crate::transcription::hardware::HardwareProfile;
use serde::{Deserialize, Serialize};

fn is_zero(v: &u64) -> bool {
    *v == 0
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum LlmTier {
    // ── Tiny / Edge ──────────────────────────────────────────────────────
    LowAlt,            // Phi-4-mini 2.5 GB
    Qwen35_2B,         // Qwen3.5-2B 1.3 GB (+ mmproj; edge multimodal)
    Phi4MiniReasoning, // Phi-4-mini-reasoning 2.5 GB (tiny STEM reasoner)
    Low,               // Qwen3-4B 2.5 GB (legacy)
    Qwen35_4B,         // Qwen3.5-4B 2.7 GB (+ mmproj; preferred tiny)
    Gemma3nE2B,        // Gemma-3n-E2B 2.8 GB (text-only via llama.cpp)
    UltraLow,          // Gemma-3n-E4B 4.5 GB
    Low2,              // DeepSeek-R1-0528-Qwen3-8B 5.0 GB
    Low3,              // Llama-3.1-8B 4.9 GB
    // ── Mid-range ─────────────────────────────────────────────────────────
    Default,           // Qwen3-8B 5.1 GB (legacy)
    Qwen35_9B,         // Qwen3.5-9B 5.7 GB (+ mmproj; preferred default)
    GlmZ1_9B,          // GLM-Z1-9B 6.2 GB (small GLM reasoner)
    Gemma4E2B,         // Gemma-4-E2B 3.1 GB (text+image, 2B effective; audio pending llama.cpp)
    Gemma4E4B,         // Gemma-4-E4B 5.0 GB (text+image, 4B effective; audio pending llama.cpp)
    Mid,               // Gemma-3-12B QAT 7.3 GB (legacy vs Gemma 4 12B)
    Gemma4_12B,        // Gemma-4-12B Unified 7.1 GB (+ mmproj; preferred mid multimodal)
    Mid2,              // Phi-4-reasoning-plus 9.1 GB
    High,              // GPT-OSS-20B 12.1 GB
    HighAlt,           // Mistral-Small-3.1-24B 14.4 GB (existing)
    Devstral24B,       // Devstral Small 2 24B 14.3 GB (coding agent)
    Coder30B,          // Qwen3-Coder-30B-A3B 18.6 GB (coding MoE, 3B active)
    Glm47Flash,        // GLM-4.7-Flash 18.3 GB (agent MoE, ~3B active)
    Gemma4A4B,         // Gemma-4-26B-A4B 17.0 GB (MoE, 4B active, text+image)
    High2,             // Qwen3.5-27B (multi-variant: IQ3_XXS / Q4_K_M)
    Gemma4_31B,        // Gemma-4-31B 18.3 GB (dense, text+image)
    High3,             // DeepSeek-R1-Distill-32B 19.85 GB
    High4,             // Gemma-3-27B 16.55 GB (legacy vs Gemma 4 26B/31B)
    VHigh,             // Qwen3.5-35B-A3B (multi-variant: IQ2_M / IQ4_XS / Q4_K_M)
    // ── Uncensored ────────────────────────────────────────────────────────
    UncensoredCompact,   // Qwen3.5-35B-A3B-Uncensored IQ2_M ~11 GB
    UncensoredBalanced,  // Qwen3.5-35B-A3B-Uncensored IQ4_XS ~18 GB
    // ── Server-grade ──────────────────────────────────────────────────────
    Server,            // Llama-3.3-70B 42.5 GB
    Server2,           // Qwen3.5-122B-A10B 39.1 GB
    Server3,           // Mistral-Small-4-119B 72.6 GB
    Server4,           // Llama-4-Scout-17B-16E 67.5 GB (split)
    Server5,           // GPT-OSS-120B 88 GB
    Server6,           // Qwen3.5-397B-A17B 115 GB
    Server7,           // Qwen3-Next-80B-A3B 48.7 GB (MoE, ~3B active)
    Server8,           // Qwen3-Coder-Next 48.7 GB (coding MoE, ~3B active)
}

/// A single quantization variant for a model that ships in multiple sizes.
/// Variants share all metadata (ratings, description, cutoff) with their parent
/// `LlmModelInfo`; only size, filename, and URL differ.
#[derive(Debug, Clone, Serialize)]
pub struct LlmModelVariant {
    /// Human-readable label shown in the UI: "Compact", "Balanced", "Quality".
    pub label: &'static str,
    /// Technical quant name for tooltip: "IQ3_XXS", "Q4_K_M", etc.
    pub quant: &'static str,
    pub filename: &'static str,
    pub disk_size_gb: f32,
    pub ram_required_gb: f32,
    pub hf_url: &'static str,
    /// Additional part URLs for split variants (empty = single file).
    pub hf_parts: &'static [&'static str],
    pub expected_size_bytes: u64,
    pub hf_part_sizes: &'static [u64],
    /// Multimodal projector filename (e.g. "mmproj-F16.gguf"). Empty = no vision.
    #[serde(skip_serializing_if = "str::is_empty")]
    pub mmproj_filename: &'static str,
    /// Download URL for the mmproj file. Empty = no vision.
    #[serde(skip_serializing_if = "str::is_empty")]
    pub mmproj_url: &'static str,
    /// Expected byte size of the mmproj file (0 = unknown or no mmproj).
    #[serde(skip_serializing_if = "is_zero")]
    pub mmproj_expected_size_bytes: u64,
}

impl LlmModelVariant {
    pub fn is_split(&self) -> bool {
        !self.hf_parts.is_empty()
    }

    pub fn all_part_urls(&self) -> Vec<&'static str> {
        let mut urls = vec![self.hf_url];
        urls.extend_from_slice(self.hf_parts);
        urls
    }

    pub fn all_part_filenames(&self) -> Vec<String> {
        if !self.is_split() {
            return vec![self.filename.to_string()];
        }
        self.all_part_urls()
            .iter()
            .map(|url| url.rsplit('/').next().unwrap_or("unknown.gguf").to_string())
            .collect()
    }
}

/// Describes how a model is hosted on HuggingFace.
///
/// Single-file models: `filename` is the single file; `hf_parts` is empty.
///
/// Split models: llama.cpp can load multi-part GGUF natively by passing the first
/// part filename. We download each part with its original HuggingFace name.
/// `filename` is the first part filename — that is what we pass to llama-server.
///
/// Multi-variant models: `variants` is non-empty. `filename` / `hf_url` /
/// `disk_size_gb` / `ram_required_gb` / `expected_size_bytes` refer to the
/// **recommended default variant** (the one selected for the user's hardware).
#[derive(Debug, Clone, Serialize)]
pub struct LlmModelInfo {
    pub tier: LlmTier,
    pub name: &'static str,
    /// Provider / organization name (e.g. "Alibaba", "OpenAI", "Meta").
    pub provider: &'static str,
    /// For single-file / default-variant models: the .gguf filename.
    /// For split models: the first part filename.
    pub filename: &'static str,
    pub disk_size_gb: f32,
    pub ram_required_gb: f32,
    // ── Ratings (0–5 scale) ───────────────────────────────────────────────
    /// General text / chat quality.
    pub text_rating: u8,
    /// Code generation and understanding.
    pub code_rating: u8,
    /// Vision / image understanding (0 = not supported by this build).
    pub vision_rating: u8,
    /// Tool / function calling reliability.
    pub tool_calling_rating: u8,
    // ── Metadata ─────────────────────────────────────────────────────────
    pub speed: &'static str,
    pub description: &'static str,
    /// Training data knowledge cutoff, e.g. "Feb 2026".
    pub knowledge_cutoff: &'static str,
    /// Link to the HuggingFace model card (hub page, not file URL).
    pub hf_model_card_url: &'static str,
    /// True for abliterated / uncensored variants.
    pub is_uncensored: bool,
    /// True for server-grade models (excluded from auto-recommendation).
    pub is_server_grade: bool,
    // ── Download URLs ─────────────────────────────────────────────────────
    /// URL for the first (or only) file / default variant.
    pub hf_url: &'static str,
    /// Additional part URLs for split models, in order. Empty = single-file.
    pub hf_parts: &'static [&'static str],
    pub context_length: u32,
    pub expected_size_bytes: u64,
    pub hf_part_sizes: &'static [u64],
    // ── Multimodal projector ──────────────────────────────────────────────
    /// Multimodal projector filename (e.g. "mmproj-F16.gguf"). Empty = no vision.
    /// When present, this file must be downloaded alongside the main model and
    /// passed to llama-server via `--mmproj`.
    #[serde(skip_serializing_if = "str::is_empty")]
    pub mmproj_filename: &'static str,
    /// Download URL for the mmproj file. Empty = no vision.
    #[serde(skip_serializing_if = "str::is_empty")]
    pub mmproj_url: &'static str,
    /// Expected byte size of the mmproj file (0 = unknown or no mmproj).
    #[serde(skip_serializing_if = "is_zero")]
    pub mmproj_expected_size_bytes: u64,
    // ── Quant variants ────────────────────────────────────────────────────
    /// Non-empty for models offered in multiple quantization sizes.
    /// The first variant is the recommended default for most users.
    pub variants: &'static [LlmModelVariant],
}

impl LlmModelInfo {
    pub fn is_split(&self) -> bool {
        !self.hf_parts.is_empty()
    }

    pub fn all_part_urls(&self) -> Vec<&'static str> {
        let mut urls = vec![self.hf_url];
        urls.extend_from_slice(self.hf_parts);
        urls
    }

    pub fn all_part_filenames(&self) -> Vec<String> {
        if !self.is_split() {
            return vec![self.filename.to_string()];
        }
        self.all_part_urls()
            .iter()
            .map(|url| url.rsplit('/').next().unwrap_or("unknown.gguf").to_string())
            .collect()
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// Variant slices (shared across parent LlmModelInfo entries)
// ─────────────────────────────────────────────────────────────────────────────

static QWEN35_27B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "IQ3_XXS",
        filename: "Qwen3.5-27B-UD-IQ3_XXS.gguf",
        disk_size_gb: 11.5,
        ram_required_gb: 14.0,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/Qwen3.5-27B-UD-IQ3_XXS.gguf",
        hf_parts: &[],
        expected_size_bytes: 12_344_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-27B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 927_607_040,
    },
    LlmModelVariant {
        label: "Balanced",
        quant: "Q4_K_M",
        filename: "Qwen3.5-27B-Q4_K_M.gguf",
        disk_size_gb: 16.7,
        ram_required_gb: 20.0,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/Qwen3.5-27B-Q4_K_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 17_933_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-27B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 927_607_040,
    },
];

static QWEN35_35B_A3B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "UD-IQ2_M",
        filename: "Qwen3.5-35B-A3B-UD-IQ2_M.gguf",
        disk_size_gb: 11.4,
        ram_required_gb: 14.0,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/Qwen3.5-35B-A3B-UD-IQ2_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 12_238_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-35B-A3B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 899_283_648,
    },
    LlmModelVariant {
        label: "Balanced",
        quant: "UD-IQ4_XS",
        filename: "Qwen3.5-35B-A3B-UD-IQ4_XS.gguf",
        disk_size_gb: 17.5,
        ram_required_gb: 22.0,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/Qwen3.5-35B-A3B-UD-IQ4_XS.gguf",
        hf_parts: &[],
        expected_size_bytes: 18_790_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-35B-A3B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 899_283_648,
    },
    LlmModelVariant {
        label: "Quality",
        quant: "Q4_K_M",
        filename: "Qwen3.5-35B-A3B-Q4_K_M.gguf",
        disk_size_gb: 22.0,
        ram_required_gb: 26.0,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/Qwen3.5-35B-A3B-Q4_K_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 23_622_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-35B-A3B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 899_283_648,
    },
];

static UNCENSORED_35B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "IQ2_M",
        filename: "Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ2_M.gguf",
        disk_size_gb: 11.0,
        ram_required_gb: 14.0,
        hf_url: "https://huggingface.co/HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ2_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 11_811_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
    },
    LlmModelVariant {
        label: "Balanced",
        quant: "IQ4_XS",
        filename: "Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_XS.gguf",
        disk_size_gb: 18.0,
        ram_required_gb: 22.0,
        hf_url: "https://huggingface.co/HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_XS.gguf",
        hf_parts: &[],
        expected_size_bytes: 19_327_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
    },
];

// ── Gemma 4 variant slices ────────────────────────────────────────────────

// Gemma 4 byte sizes verified via huggingface.co API (X-Linked-Size) on
// 2026-05-08. mmproj F16 chosen over BF16 / Q8_0 because F16 has the widest
// platform support — it works on Apple Silicon M1 (no native BF16), x86_64
// Vulkan, and CUDA Hopper / Ampere / Ada / Blackwell. BF16 mmproj triggers
// a known Metal "cooperative tensor types" error on some macOS versions; Q8_0
// mmproj exists only in ggml-org repos.
static GEMMA4_E2B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "Q4_K_M",
        filename: "gemma-4-E2B-it-Q4_K_M.gguf",
        disk_size_gb: 3.1,
        ram_required_gb: 5.5,
        hf_url: "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 3_106_736_256,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-E2B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 985_654_080,
    },
    LlmModelVariant {
        label: "Quality",
        quant: "Q8_0",
        filename: "gemma-4-E2B-it-Q8_0.gguf",
        disk_size_gb: 5.0,
        ram_required_gb: 7.5,
        hf_url: "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q8_0.gguf",
        hf_parts: &[],
        expected_size_bytes: 5_048_350_848,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-E2B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 985_654_080,
    },
];

static GEMMA4_E4B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "Q4_K_M",
        filename: "gemma-4-E4B-it-Q4_K_M.gguf",
        disk_size_gb: 5.0,
        ram_required_gb: 7.5,
        hf_url: "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 4_977_169_568,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-E4B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 990_372_672,
    },
    LlmModelVariant {
        label: "Quality",
        quant: "Q8_0",
        filename: "gemma-4-E4B-it-Q8_0.gguf",
        disk_size_gb: 8.2,
        ram_required_gb: 10.5,
        hf_url: "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q8_0.gguf",
        hf_parts: &[],
        expected_size_bytes: 8_192_951_456,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-E4B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 990_372_672,
    },
];

static GEMMA4_A4B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "UD-Q4_K_M",
        filename: "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        disk_size_gb: 17.0,
        ram_required_gb: 20.0,
        hf_url: "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 16_947_539_744,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-26B-A4B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 1_193_058_784,
    },
    LlmModelVariant {
        label: "Quality",
        quant: "Q8_0",
        filename: "gemma-4-26B-A4B-it-Q8_0.gguf",
        disk_size_gb: 26.9,
        ram_required_gb: 30.0,
        hf_url: "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-Q8_0.gguf",
        hf_parts: &[],
        expected_size_bytes: 26_859_859_744,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-26B-A4B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 1_193_058_784,
    },
];

static GEMMA4_31B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "Q4_K_M",
        filename: "gemma-4-31B-it-Q4_K_M.gguf",
        disk_size_gb: 18.3,
        ram_required_gb: 21.0,
        hf_url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 18_323_731_456,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-31B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 1_198_957_024,
    },
    LlmModelVariant {
        label: "Quality",
        quant: "Q8_0",
        filename: "gemma-4-31B-it-Q8_0.gguf",
        disk_size_gb: 32.6,
        ram_required_gb: 36.0,
        hf_url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q8_0.gguf",
        hf_parts: &[],
        expected_size_bytes: 32_635_675_648,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-31B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 1_198_957_024,
    },
];

static GEMMA4_12B_VARIANTS: &[LlmModelVariant] = &[
    LlmModelVariant {
        label: "Compact",
        quant: "Q4_K_M",
        filename: "gemma-4-12b-it-Q4_K_M.gguf",
        disk_size_gb: 7.1,
        ram_required_gb: 9.0,
        hf_url: "https://huggingface.co/unsloth/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12b-it-Q4_K_M.gguf",
        hf_parts: &[],
        expected_size_bytes: 7_121_860_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-12B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-12B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 175_115_840,
    },
    LlmModelVariant {
        label: "Quality",
        quant: "Q8_0",
        filename: "gemma-4-12b-it-Q8_0.gguf",
        disk_size_gb: 12.7,
        ram_required_gb: 15.0,
        hf_url: "https://huggingface.co/unsloth/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12b-it-Q8_0.gguf",
        hf_parts: &[],
        expected_size_bytes: 12_669_646_240,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-12B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-12B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 175_115_840,
    },
];

// ─────────────────────────────────────────────────────────────────────────────
// Model catalog
// Verified against HuggingFace API + HEAD requests where noted.
// ─────────────────────────────────────────────────────────────────────────────
pub const LLM_MODELS: &[LlmModelInfo] = &[
    // ── Tiny / Edge ──────────────────────────────────────────────────────────

    LlmModelInfo {
        tier: LlmTier::LowAlt,
        name: "Phi 4 Mini",
        provider: "Microsoft",
        filename: "microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
        disk_size_gb: 2.5,
        ram_required_gb: 3.5,
        text_rating: 2,
        code_rating: 2,
        vision_rating: 0,
        tool_calling_rating: 3,
        speed: "Very fast",
        description: "Microsoft's smallest model. Fastest option for very low-RAM machines.",
        knowledge_cutoff: "Mar 2025",
        hf_model_card_url: "https://huggingface.co/microsoft/Phi-4-mini-instruct",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/bartowski/microsoft_Phi-4-mini-instruct-GGUF/resolve/main/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 8192,
        expected_size_bytes: 2_491_874_688,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Phi4MiniReasoning,
        name: "Phi 4 Mini Reasoning",
        provider: "Microsoft",
        filename: "Phi-4-mini-reasoning-Q4_K_M.gguf",
        disk_size_gb: 2.5,
        ram_required_gb: 3.5,
        text_rating: 2,
        code_rating: 3,
        vision_rating: 0,
        tool_calling_rating: 2,
        speed: "Very fast",
        description: "Tiny STEM/math reasoner. Same footprint as Phi 4 Mini but tuned for step-by-step reasoning.",
        knowledge_cutoff: "Apr 2025",
        hf_model_card_url: "https://huggingface.co/microsoft/Phi-4-mini-reasoning",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Phi-4-mini-reasoning-GGUF/resolve/main/Phi-4-mini-reasoning-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 32768,
        expected_size_bytes: 2_491_875_232,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Qwen35_2B,
        name: "Qwen 3.5 2B",
        provider: "Alibaba",
        filename: "Qwen3.5-2B-Q4_K_M.gguf",
        disk_size_gb: 1.3,
        ram_required_gb: 3.0,
        text_rating: 2,
        code_rating: 2,
        vision_rating: 3,
        tool_calling_rating: 3,
        speed: "Very fast",
        description: "Smallest Qwen 3.5 with native vision. Ideal for phones, old laptops, and ultra-low RAM machines.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3.5-2B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 1_280_835_840,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-2B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 668_227_264,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Low,
        name: "Qwen 3 4B",
        provider: "Alibaba",
        filename: "Qwen3-4B-Q4_K_M.gguf",
        disk_size_gb: 2.5,
        ram_required_gb: 4.0,
        text_rating: 2,
        code_rating: 2,
        vision_rating: 0,
        tool_calling_rating: 3,
        speed: "Fast",
        description: "Compact model with strong tool calling. Prefer Qwen 3.5 4B when available.",
        knowledge_cutoff: "Sep 2024",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3-4B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 8192,
        expected_size_bytes: 2_497_280_256,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Qwen35_4B,
        name: "Qwen 3.5 4B",
        provider: "Alibaba",
        filename: "Qwen3.5-4B-Q4_K_M.gguf",
        disk_size_gb: 2.7,
        ram_required_gb: 4.5,
        text_rating: 3,
        code_rating: 3,
        vision_rating: 3,
        tool_calling_rating: 4,
        speed: "Fast",
        description: "Best tiny multimodal model. Strong tools + vision in ~2.7 GB — preferred over Qwen 3 4B.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3.5-4B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 2_740_937_888,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-4B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 672_423_616,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Gemma3nE2B,
        name: "Gemma 3n E2B",
        provider: "Google",
        filename: "gemma-3n-E2B-it-Q4_K_M.gguf",
        disk_size_gb: 2.82,
        ram_required_gb: 4.0,
        text_rating: 2,
        code_rating: 1,
        vision_rating: 0,
        tool_calling_rating: 2,
        speed: "Very fast",
        description: "Google's smallest on-device Gemma 3n. Text-only via llama.cpp currently. Completes the 3n edge pair with E4B.",
        knowledge_cutoff: "Mar 2025",
        hf_model_card_url: "https://huggingface.co/google/gemma-3n-E2B-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-3n-E2B-it-GGUF/resolve/main/gemma-3n-E2B-it-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 32768,
        expected_size_bytes: 3_026_881_888,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Gemma4E2B,
        name: "Gemma 4 E2B",
        provider: "Google",
        filename: "gemma-4-E2B-it-Q4_K_M.gguf",
        disk_size_gb: 3.1,
        ram_required_gb: 5.5,
        text_rating: 2,
        code_rating: 2,
        vision_rating: 3,
        tool_calling_rating: 2,
        speed: "Fast",
        description: "Google's smallest Gemma 4. Multimodal: text + image input via llama.cpp (audio support pending upstream). 2B effective params with PLE. Ideal for edge devices.",
        knowledge_cutoff: "Apr 2026",
        hf_model_card_url: "https://huggingface.co/google/gemma-4-E2B-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 3_106_736_256,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-E2B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 985_654_080,
        variants: GEMMA4_E2B_VARIANTS,
    },

    LlmModelInfo {
        tier: LlmTier::UltraLow,
        name: "Gemma 3n E4B",
        provider: "Google",
        filename: "gemma-3n-E4B-it-Q4_K_M.gguf",
        disk_size_gb: 4.54,
        ram_required_gb: 6.0,
        text_rating: 2,
        code_rating: 1,
        vision_rating: 0,
        tool_calling_rating: 2,
        speed: "Fast",
        description: "Google's ultra-efficient on-device model. Designed for phones and edge devices. Text-only via llama.cpp currently.",
        knowledge_cutoff: "Mar 2025",
        hf_model_card_url: "https://huggingface.co/google/gemma-3n-E4B-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-3n-E4B-it-GGUF/resolve/main/gemma-3n-E4B-it-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 32768,
        expected_size_bytes: 4_876_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Gemma4E4B,
        name: "Gemma 4 E4B",
        provider: "Google",
        filename: "gemma-4-E4B-it-Q4_K_M.gguf",
        disk_size_gb: 5.0,
        ram_required_gb: 7.5,
        text_rating: 3,
        code_rating: 2,
        vision_rating: 4,
        tool_calling_rating: 2,
        speed: "Fast",
        description: "Gemma 4 multimodal: text + image input via llama.cpp (audio support pending upstream). 4B effective params. Excellent vision quality for its size. 128K context.",
        knowledge_cutoff: "Apr 2026",
        hf_model_card_url: "https://huggingface.co/google/gemma-4-E4B-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 4_977_169_568,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-E4B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 990_372_672,
        variants: GEMMA4_E4B_VARIANTS,
    },

    LlmModelInfo {
        tier: LlmTier::Low2,
        name: "DeepSeek R1 0528 Qwen3 8B",
        provider: "DeepSeek",
        filename: "DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf",
        disk_size_gb: 5.0,
        ram_required_gb: 6.5,
        text_rating: 3,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 3,
        speed: "Fast",
        description: "Latest R1 distill on Qwen3-8B (May 2025). Stronger reasoning than the older Llama-8B distill at the same size.",
        knowledge_cutoff: "May 2025",
        hf_model_card_url: "https://huggingface.co/deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/DeepSeek-R1-0528-Qwen3-8B-GGUF/resolve/main/DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 5_027_785_216,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Low3,
        name: "Llama 3.1 8B",
        provider: "Meta",
        filename: "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        disk_size_gb: 4.92,
        ram_required_gb: 6.0,
        text_rating: 2,
        code_rating: 2,
        vision_rating: 0,
        tool_calling_rating: 3,
        speed: "Fast",
        description: "Meta's reliable 8B workhorse with 128k context. Wide ecosystem support, excellent tool calling.",
        knowledge_cutoff: "Mar 2023",
        hf_model_card_url: "https://huggingface.co/meta-llama/Meta-Llama-3.1-8B-Instruct",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 5_284_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    // ── Mid-range ─────────────────────────────────────────────────────────────

    LlmModelInfo {
        tier: LlmTier::Default,
        name: "Qwen 3 8B",
        provider: "Alibaba",
        filename: "Qwen3-8B-Q4_K_M.gguf",
        disk_size_gb: 5.1,
        ram_required_gb: 6.5,
        text_rating: 3,
        code_rating: 3,
        vision_rating: 0,
        tool_calling_rating: 4,
        speed: "Medium",
        description: "Solid mid-size generalist. Prefer Qwen 3.5 9B when available (vision + newer quality).",
        knowledge_cutoff: "Sep 2024",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3-8B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 8192,
        expected_size_bytes: 5_027_783_488,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Qwen35_9B,
        name: "Qwen 3.5 9B",
        provider: "Alibaba",
        filename: "Qwen3.5-9B-Q4_K_M.gguf",
        disk_size_gb: 5.7,
        ram_required_gb: 8.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 4,
        tool_calling_rating: 4,
        speed: "Medium",
        description: "Recommended default. Dense 9B with native vision, strong tools, and 262K context — best everyday local model.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3.5-9B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 5_680_522_464,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-9B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 918_166_080,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::GlmZ1_9B,
        name: "GLM Z1 9B",
        provider: "Z.ai",
        filename: "GLM-Z1-9B-0414-Q4_K_M.gguf",
        disk_size_gb: 6.2,
        ram_required_gb: 8.0,
        text_rating: 3,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 4,
        speed: "Medium",
        description: "Compact GLM reasoner. Fits 8 GB machines — the small sibling to GLM 4.7 Flash for mid-RAM agent/reasoning workloads.",
        knowledge_cutoff: "Apr 2025",
        hf_model_card_url: "https://huggingface.co/THUDM/GLM-Z1-9B-0414",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/GLM-Z1-9B-0414-GGUF/resolve/main/GLM-Z1-9B-0414-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 32768,
        expected_size_bytes: 6_166_575_232,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Mid,
        name: "Gemma 3 12B",
        provider: "Google",
        filename: "google_gemma-3-12b-it-qat-Q4_K_M.gguf",
        disk_size_gb: 7.3,
        ram_required_gb: 10.0,
        text_rating: 3,
        code_rating: 3,
        vision_rating: 3,
        tool_calling_rating: 3,
        speed: "Medium",
        description: "Google's mid-size multimodal model with QAT quantization. Prefer Gemma 4 12B for newer quality and Apache-2.0.",
        knowledge_cutoff: "Mar 2025",
        hf_model_card_url: "https://huggingface.co/google/gemma-3-12b-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/bartowski/google_gemma-3-12b-it-qat-GGUF/resolve/main/google_gemma-3-12b-it-qat-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 7_840_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Gemma4_12B,
        name: "Gemma 4 12B",
        provider: "Google",
        filename: "gemma-4-12b-it-Q4_K_M.gguf",
        disk_size_gb: 7.1,
        ram_required_gb: 9.0,
        text_rating: 4,
        code_rating: 3,
        vision_rating: 4,
        tool_calling_rating: 3,
        speed: "Medium",
        description: "Gemma 4 Unified 12B — the missing mid-size. Text + image, 256K context, compact mmproj (~167 MB). Best Gemma for 8–16 GB machines.",
        knowledge_cutoff: "Jun 2026",
        hf_model_card_url: "https://huggingface.co/google/gemma-4-12B-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12b-it-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 7_121_860_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-12B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-12B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 175_115_840,
        variants: GEMMA4_12B_VARIANTS,
    },

    LlmModelInfo {
        tier: LlmTier::Mid2,
        name: "Phi 4 Reasoning Plus",
        provider: "Microsoft",
        filename: "Phi-4-reasoning-plus-Q4_K_M.gguf",
        disk_size_gb: 9.1,
        ram_required_gb: 12.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 3,
        speed: "Medium",
        description: "Microsoft's upgraded 14B reasoner. Stronger STEM/code than the original Phi-4-reasoning at the same size class.",
        knowledge_cutoff: "Apr 2025",
        hf_model_card_url: "https://huggingface.co/microsoft/Phi-4-reasoning-plus",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Phi-4-reasoning-plus-GGUF/resolve/main/Phi-4-reasoning-plus-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 32768,
        expected_size_bytes: 9_053_117_120,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::High,
        name: "GPT OSS 20B",
        provider: "OpenAI",
        filename: "openai_gpt-oss-20b-MXFP4.gguf",
        disk_size_gb: 12.1,
        ram_required_gb: 16.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 4,
        speed: "Medium",
        description: "OpenAI's open-source reasoning model — equivalent to o3-mini. Only 3.6B active params (MoE). Apache 2.0.",
        knowledge_cutoff: "Mid 2025",
        hf_model_card_url: "https://huggingface.co/openai/gpt-oss-20b",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/bartowski/openai_gpt-oss-20b-GGUF/resolve/main/openai_gpt-oss-20b-MXFP4.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 12_991_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::HighAlt,
        name: "Mistral Small 3.1 24B",
        provider: "Mistral",
        filename: "Mistral-Small-3.1-24B-Instruct-2503-Q4_K_M.gguf",
        disk_size_gb: 14.4,
        ram_required_gb: 16.0,
        text_rating: 3,
        code_rating: 3,
        vision_rating: 2,
        tool_calling_rating: 4,
        speed: "GPU recommended",
        description: "Mistral's dense 24B with strong tool calling and 128k context. Excellent for agents.",
        knowledge_cutoff: "Mar 2025",
        hf_model_card_url: "https://huggingface.co/mistralai/Mistral-Small-3.1-24B-Instruct-2503",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/lmstudio-community/Mistral-Small-3.1-24B-Instruct-2503-GGUF/resolve/main/Mistral-Small-3.1-24B-Instruct-2503-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 4096,
        expected_size_bytes: 14_333_910_176,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Devstral24B,
        name: "Devstral Small 2 24B",
        provider: "Mistral",
        filename: "mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf",
        disk_size_gb: 14.3,
        ram_required_gb: 16.0,
        text_rating: 3,
        code_rating: 5,
        vision_rating: 0,
        tool_calling_rating: 4,
        speed: "GPU recommended",
        description: "Mistral's agentic coding specialist. Dense 24B tuned for software engineering and tool use.",
        knowledge_cutoff: "Nov 2025",
        hf_model_card_url: "https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/bartowski/mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF/resolve/main/mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 14_334_438_272,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Coder30B,
        name: "Qwen 3 Coder 30B A3B",
        provider: "Alibaba",
        filename: "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
        disk_size_gb: 18.6,
        ram_required_gb: 22.0,
        text_rating: 3,
        code_rating: 5,
        vision_rating: 0,
        tool_calling_rating: 5,
        speed: "GPU recommended",
        description: "Coding MoE: 30B total / 3B active. Best agentic coding model in the 16–24 GB band. 256K context.",
        knowledge_cutoff: "Jul 2025",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3-Coder-30B-A3B-Instruct",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF/resolve/main/Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 18_556_689_568,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Glm47Flash,
        name: "GLM 4.7 Flash",
        provider: "Z.ai",
        filename: "GLM-4.7-Flash-Q4_K_M.gguf",
        disk_size_gb: 18.3,
        ram_required_gb: 22.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 5,
        speed: "GPU recommended",
        description: "Z.ai agent MoE (~30B / ~3B active). Strong tool calling and SWE-style agent workloads — non-Qwen alternative to 35B-A3B.",
        knowledge_cutoff: "Jan 2026",
        hf_model_card_url: "https://huggingface.co/zai-org/GLM-4.7-Flash",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/GLM-4.7-Flash-GGUF/resolve/main/GLM-4.7-Flash-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 202752,
        expected_size_bytes: 18_312_339_808,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Gemma4A4B,
        name: "Gemma 4 26B A4B",
        provider: "Google",
        filename: "gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        disk_size_gb: 17.0,
        ram_required_gb: 20.0,
        text_rating: 4,
        code_rating: 3,
        vision_rating: 4,
        tool_calling_rating: 3,
        speed: "GPU recommended",
        description: "Gemma 4 MoE: 26B total / 4B active. Near-31B quality at 4B speed. Text + image multimodal. 256K context. Best value Gemma 4.",
        knowledge_cutoff: "Apr 2026",
        hf_model_card_url: "https://huggingface.co/google/gemma-4-26B-A4B-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 16_947_539_744,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-26B-A4B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-26B-A4B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 1_193_058_784,
        variants: GEMMA4_A4B_VARIANTS,
    },

    LlmModelInfo {
        tier: LlmTier::High2,
        name: "Qwen 3.5 27B",
        provider: "Alibaba",
        // Default variant: Compact (IQ3_XXS) — fits more hardware
        filename: "Qwen3.5-27B-UD-IQ3_XXS.gguf",
        disk_size_gb: 11.5,
        ram_required_gb: 14.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 3,
        tool_calling_rating: 4,
        speed: "GPU recommended",
        description: "Dense 27B with native vision. Arena rank #24 among all open-source models. Choose your size below.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3.5-27B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/Qwen3.5-27B-UD-IQ3_XXS.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 12_344_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-27B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 927_607_040,
        variants: QWEN35_27B_VARIANTS,
    },

    LlmModelInfo {
        tier: LlmTier::Gemma4_31B,
        name: "Gemma 4 31B",
        provider: "Google",
        filename: "gemma-4-31B-it-Q4_K_M.gguf",
        disk_size_gb: 18.3,
        ram_required_gb: 21.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 5,
        tool_calling_rating: 3,
        speed: "GPU recommended",
        description: "Google's flagship Gemma 4. Dense 31B with top-tier vision. Text + image multimodal. 256K context. Built-in thinking mode.",
        knowledge_cutoff: "Apr 2026",
        hf_model_card_url: "https://huggingface.co/google/gemma-4-31B-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/main/gemma-4-31B-it-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 18_323_731_456,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-gemma-4-31B-it-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/gemma-4-31B-it-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 1_198_957_024,
        variants: GEMMA4_31B_VARIANTS,
    },

    LlmModelInfo {
        tier: LlmTier::High3,
        name: "DeepSeek R1 Distill 32B",
        provider: "DeepSeek",
        filename: "DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf",
        disk_size_gb: 19.85,
        ram_required_gb: 24.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 3,
        speed: "GPU recommended",
        description: "DeepSeek's 32B reasoning distill. Exceptional chain-of-thought for its size. Fits in 24 GB VRAM.",
        knowledge_cutoff: "Jul 2024",
        hf_model_card_url: "https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/bartowski/DeepSeek-R1-Distill-Qwen-32B-GGUF/resolve/main/DeepSeek-R1-Distill-Qwen-32B-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 21_313_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::High4,
        name: "Gemma 3 27B",
        provider: "Google",
        filename: "google_gemma-3-27b-it-Q4_K_M.gguf",
        disk_size_gb: 16.55,
        ram_required_gb: 20.0,
        text_rating: 4,
        code_rating: 3,
        vision_rating: 4,
        tool_calling_rating: 3,
        speed: "GPU recommended",
        description: "Google's best open multimodal model from the Gemma 3 line. Prefer Gemma 4 26B A4B / 31B for newer quality.",
        knowledge_cutoff: "Mar 2025",
        hf_model_card_url: "https://huggingface.co/google/gemma-3-27b-it",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/gemma-3-27b-it-GGUF/resolve/main/gemma-3-27b-it-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 17_773_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::VHigh,
        name: "Qwen 3.5 35B A3B",
        provider: "Alibaba",
        // Default variant: Balanced (IQ4_XS)
        filename: "Qwen3.5-35B-A3B-UD-IQ4_XS.gguf",
        disk_size_gb: 17.5,
        ram_required_gb: 22.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 4,
        tool_calling_rating: 4,
        speed: "GPU recommended",
        description: "Top open-source MoE model. Only 3B active params — inference speed of a 3B with quality far above. Arena rank #28. Choose your size below.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3.5-35B-A3B",
        is_uncensored: false,
        is_server_grade: false,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/Qwen3.5-35B-A3B-UD-IQ4_XS.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 18_790_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "mmproj-Qwen3.5-35B-A3B-F16.gguf",
        mmproj_url: "https://huggingface.co/unsloth/Qwen3.5-35B-A3B-GGUF/resolve/main/mmproj-F16.gguf",
        mmproj_expected_size_bytes: 899_283_648,
        variants: QWEN35_35B_A3B_VARIANTS,
    },

    // ── Uncensored ────────────────────────────────────────────────────────────

    LlmModelInfo {
        tier: LlmTier::UncensoredCompact,
        name: "Qwen 3.5 35B Uncensored (Compact)",
        provider: "HauhauCS",
        filename: "Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ2_M.gguf",
        disk_size_gb: 11.0,
        ram_required_gb: 14.0,
        text_rating: 4,
        code_rating: 3,
        vision_rating: 3,
        tool_calling_rating: 3,
        speed: "Medium",
        description: "Uncensored Qwen3.5-35B MoE (abliterated). No refusals. Essential for sensitive monitoring, analysis, and tasks standard models decline.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive",
        is_uncensored: true,
        is_server_grade: false,
        hf_url: "https://huggingface.co/HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ2_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 11_811_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: UNCENSORED_35B_VARIANTS,
    },

    LlmModelInfo {
        tier: LlmTier::UncensoredBalanced,
        name: "Qwen 3.5 35B Uncensored (Balanced)",
        provider: "HauhauCS",
        filename: "Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_XS.gguf",
        disk_size_gb: 18.0,
        ram_required_gb: 22.0,
        text_rating: 4,
        code_rating: 3,
        vision_rating: 3,
        tool_calling_rating: 3,
        speed: "GPU recommended",
        description: "Higher quality uncensored Qwen3.5-35B MoE (abliterated). Better outputs for sensitive analysis tasks.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive",
        is_uncensored: true,
        is_server_grade: false,
        hf_url: "https://huggingface.co/HauhauCS/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive/resolve/main/Qwen3.5-35B-A3B-Uncensored-HauhauCS-Aggressive-IQ4_XS.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 19_327_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: UNCENSORED_35B_VARIANTS,
    },

    // ── Server-grade ──────────────────────────────────────────────────────────

    LlmModelInfo {
        tier: LlmTier::Server,
        name: "Llama 3.3 70B",
        provider: "Meta",
        filename: "Llama-3.3-70B-Instruct-Q4_K_M.gguf",
        disk_size_gb: 42.5,
        ram_required_gb: 48.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 4,
        speed: "Server GPU",
        description: "Meta's best dense 70B. Rivals much larger models on benchmarks. Requires 48 GB+ VRAM.",
        knowledge_cutoff: "Dec 2023",
        hf_model_card_url: "https://huggingface.co/meta-llama/Llama-3.3-70B-Instruct",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/unsloth/Llama-3.3-70B-Instruct-GGUF/resolve/main/Llama-3.3-70B-Instruct-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 45_618_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Server2,
        name: "Qwen 3.5 122B A10B",
        provider: "Alibaba",
        filename: "Qwen3.5-122B-A10B-UD-IQ2_M.gguf",
        disk_size_gb: 39.1,
        ram_required_gb: 48.0,
        text_rating: 5,
        code_rating: 5,
        vision_rating: 4,
        tool_calling_rating: 5,
        speed: "Server GPU",
        description: "122B MoE, only 10B active. Near-frontier quality accessible on 48 GB+ servers. Arena rank #16 globally.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3.5-122B-A10B",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-122B-A10B-GGUF/resolve/main/Qwen3.5-122B-A10B-UD-IQ2_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 41_980_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Server3,
        name: "Mistral Small 4 119B",
        provider: "Mistral",
        filename: "Mistral-Small-4-119B-2603-Q4_K_M.gguf",
        disk_size_gb: 72.6,
        ram_required_gb: 80.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 3,
        tool_calling_rating: 4,
        speed: "Server GPU",
        description: "Mistral's latest unified MoE — reasoning + multimodal + coding in one 256k-context model. Released March 2026.",
        knowledge_cutoff: "Mar 2026",
        hf_model_card_url: "https://huggingface.co/mistralai/Mistral-Small-4-119B-2603",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/unsloth/Mistral-Small-4-119B-2603-GGUF/resolve/main/Mistral-Small-4-119B-2603-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 77_952_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Server4,
        name: "Llama 4 Scout 17B",
        provider: "Meta",
        // Split: 2 parts
        filename: "Llama-4-Scout-17B-16E-Instruct-Q4_K_M-00001-of-00002.gguf",
        disk_size_gb: 67.5,
        ram_required_gb: 80.0,
        text_rating: 4,
        code_rating: 4,
        vision_rating: 4,
        tool_calling_rating: 4,
        speed: "Server GPU",
        description: "Meta's Llama 4 MoE with unprecedented 10M token context. Multimodal (text + image). 109B total / 17B active.",
        knowledge_cutoff: "Mar 2025",
        hf_model_card_url: "https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/unsloth/Llama-4-Scout-17B-16E-Instruct-GGUF/resolve/main/Llama-4-Scout-17B-16E-Instruct-Q4_K_M-00001-of-00002.gguf",
        hf_parts: &[
            "https://huggingface.co/unsloth/Llama-4-Scout-17B-16E-Instruct-GGUF/resolve/main/Llama-4-Scout-17B-16E-Instruct-Q4_K_M-00002-of-00002.gguf",
        ],
        context_length: 131072,
        expected_size_bytes: 36_283_000_000,
        hf_part_sizes: &[31_217_000_000],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Server5,
        name: "GPT OSS 120B",
        provider: "OpenAI",
        filename: "openai_gpt-oss-120b-Q4_K_M.gguf",
        disk_size_gb: 88.0,
        ram_required_gb: 96.0,
        text_rating: 5,
        code_rating: 5,
        vision_rating: 0,
        tool_calling_rating: 5,
        speed: "Server GPU",
        description: "OpenAI's largest open-source model. Near o4-mini parity. Best open-weight reasoning model available. 128k context.",
        knowledge_cutoff: "Mid 2025",
        hf_model_card_url: "https://huggingface.co/openai/gpt-oss-120b",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/bartowski/openai_gpt-oss-120b-GGUF/resolve/main/openai_gpt-oss-120b-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 131072,
        expected_size_bytes: 94_489_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Server6,
        name: "Qwen 3.5 397B A17B",
        provider: "Alibaba",
        filename: "Qwen3.5-397B-A17B-UD-IQ2_XXS.gguf",
        disk_size_gb: 115.0,
        ram_required_gb: 128.0,
        text_rating: 5,
        code_rating: 5,
        vision_rating: 5,
        tool_calling_rating: 5,
        speed: "Server GPU",
        description: "The best open-source model in the world. Arena rank #3 globally. 397B total / 17B active MoE. Near-frontier on every task.",
        knowledge_cutoff: "Feb 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3.5-397B-A17B",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/unsloth/Qwen3.5-397B-A17B-GGUF/resolve/main/Qwen3.5-397B-A17B-UD-IQ2_XXS.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 123_476_000_000,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Server7,
        name: "Qwen 3 Next 80B A3B",
        provider: "Alibaba",
        filename: "Qwen_Qwen3-Next-80B-A3B-Instruct-Q4_K_M.gguf",
        disk_size_gb: 48.7,
        ram_required_gb: 56.0,
        text_rating: 5,
        code_rating: 4,
        vision_rating: 0,
        tool_calling_rating: 5,
        speed: "Server GPU",
        description: "80B MoE with only ~3B active. Fast long-context server model — sits between Scout and Qwen 3.5 122B A10B.",
        knowledge_cutoff: "Sep 2025",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3-Next-80B-A3B-Instruct",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/bartowski/Qwen_Qwen3-Next-80B-A3B-Instruct-GGUF/resolve/main/Qwen_Qwen3-Next-80B-A3B-Instruct-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 48_727_677_408,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },

    LlmModelInfo {
        tier: LlmTier::Server8,
        name: "Qwen 3 Coder Next",
        provider: "Alibaba",
        filename: "Qwen_Qwen3-Coder-Next-Q4_K_M.gguf",
        disk_size_gb: 48.7,
        ram_required_gb: 56.0,
        text_rating: 4,
        code_rating: 5,
        vision_rating: 0,
        tool_calling_rating: 5,
        speed: "Server GPU",
        description: "Server-grade coding MoE (~80B / ~3B active). Best practical coding beast for Mac Studio / 64GB+ boxes.",
        knowledge_cutoff: "Jan 2026",
        hf_model_card_url: "https://huggingface.co/Qwen/Qwen3-Coder-Next",
        is_uncensored: false,
        is_server_grade: true,
        hf_url: "https://huggingface.co/bartowski/Qwen_Qwen3-Coder-Next-GGUF/resolve/main/Qwen_Qwen3-Coder-Next-Q4_K_M.gguf",
        hf_parts: &[],
        context_length: 262144,
        expected_size_bytes: 48_727_680_704,
        hf_part_sizes: &[],
        mmproj_filename: "",
        mmproj_url: "",
        mmproj_expected_size_bytes: 0,
        variants: &[],
    },
];

// ─────────────────────────────────────────────────────────────────────────────
// Hardware selection — auto-recommendation only
// Server-grade and uncensored tiers are never auto-recommended.
// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct LlmModelSelection {
    pub tier: LlmTier,
    pub filename: &'static str,
    pub name: &'static str,
    pub disk_size_gb: f32,
    pub reason: String,
    pub can_upgrade: bool,
    pub gpu_layers: i32,
}

pub fn select_llm_model(hw: &HardwareProfile) -> LlmModelSelection {
    let total_ram_gb = hw.total_ram_mb as f32 / 1024.0;
    let gpu_vram_gb = hw.gpu_vram_mb.map(|v| v as f32 / 1024.0).unwrap_or(0.0);

    let (tier, reason) = select_tier(hw, total_ram_gb, gpu_vram_gb);
    let gpu_layers = compute_gpu_layers(hw, gpu_vram_gb);
    let can_upgrade = can_upgrade_tier(&tier, total_ram_gb, gpu_vram_gb, hw.is_apple_silicon);

    let model = get_model_info(&tier);

    LlmModelSelection {
        tier,
        filename: model.filename,
        name: model.name,
        disk_size_gb: model.disk_size_gb,
        reason,
        can_upgrade,
        gpu_layers,
    }
}

fn select_tier(hw: &HardwareProfile, total_ram_gb: f32, gpu_vram_gb: f32) -> (LlmTier, String) {
    // ── Apple Silicon (Metal, unified memory) ────────────────────────────────
    if hw.is_apple_silicon {
        if total_ram_gb >= 36.0 {
            return (
                LlmTier::VHigh,
                format!(
                    "Apple Silicon with {:.0}GB RAM — Qwen 3.5 35B A3B (MoE) runs excellently with Metal",
                    total_ram_gb
                ),
            );
        }
        if total_ram_gb >= 24.0 {
            return (
                LlmTier::High2,
                format!(
                    "Apple Silicon with {:.0}GB RAM — Qwen 3.5 27B recommended with Metal acceleration",
                    total_ram_gb
                ),
            );
        }
        if total_ram_gb >= 16.0 {
            return (
                LlmTier::High,
                "Apple Silicon with 16GB RAM — GPT OSS 20B recommended (12 GB, Metal accelerated)".to_string(),
            );
        }
        if total_ram_gb >= 10.0 {
            return (
                LlmTier::Qwen35_9B,
                "Apple Silicon with 10GB+ RAM — Qwen 3.5 9B recommended (vision + tools, Metal)".to_string(),
            );
        }
        if total_ram_gb >= 8.0 {
            return (
                LlmTier::Qwen35_9B,
                "Apple Silicon with 8GB RAM — Qwen 3.5 9B recommended with Metal acceleration".to_string(),
            );
        }
        if total_ram_gb >= 6.0 {
            return (
                LlmTier::Qwen35_4B,
                format!(
                    "Apple Silicon with {:.0}GB RAM — Qwen 3.5 4B recommended (vision + tools)",
                    total_ram_gb
                ),
            );
        }
        if total_ram_gb >= 4.0 {
            return (
                LlmTier::Qwen35_2B,
                format!(
                    "Apple Silicon with {:.0}GB RAM — Qwen 3.5 2B recommended for edge devices",
                    total_ram_gb
                ),
            );
        }
        return (
            LlmTier::LowAlt,
            format!(
                "Apple Silicon with {:.0}GB RAM — Phi 4 Mini is the smallest supported model",
                total_ram_gb
            ),
        );
    }

    // ── Dedicated GPU (CUDA or Vulkan) ────────────────────────────────────────
    if hw.supports_cuda || hw.supports_vulkan {
        let backend = if hw.supports_cuda { "CUDA" } else { "Vulkan" };

        if gpu_vram_gb >= 36.0 {
            return (
                LlmTier::VHigh,
                format!(
                    "{} GPU with {:.0}GB VRAM — Qwen 3.5 35B A3B MoE fits with full GPU offload",
                    backend, gpu_vram_gb
                ),
            );
        }
        if gpu_vram_gb >= 16.0 {
            return (
                LlmTier::High2,
                format!(
                    "{} GPU with {:.0}GB VRAM — Qwen 3.5 27B recommended with full GPU offload",
                    backend, gpu_vram_gb
                ),
            );
        }
        if gpu_vram_gb >= 12.0 {
            return (
                LlmTier::High,
                format!(
                    "{} GPU with {:.0}GB VRAM — GPT OSS 20B fits with full GPU offload",
                    backend, gpu_vram_gb
                ),
            );
        }
        if gpu_vram_gb >= 8.0 {
            return (
                LlmTier::Qwen35_9B,
                format!(
                    "{} GPU with {:.0}GB VRAM — Qwen 3.5 9B recommended with full GPU offload",
                    backend, gpu_vram_gb
                ),
            );
        }
        if gpu_vram_gb >= 5.0 {
            return (
                LlmTier::Qwen35_9B,
                format!(
                    "{} GPU with {:.0}GB VRAM — Qwen 3.5 9B fits with full GPU offload",
                    backend, gpu_vram_gb
                ),
            );
        }
        if gpu_vram_gb >= 2.0 {
            return (
                LlmTier::Qwen35_4B,
                format!(
                    "{} GPU with {:.0}GB VRAM — Qwen 3.5 4B with partial GPU offload (faster than pure CPU)",
                    backend, gpu_vram_gb
                ),
            );
        }
        return (
            LlmTier::Qwen35_4B,
            format!(
                "{} GPU detected — using compact Qwen 3.5 4B model; GPU acceleration active",
                backend
            ),
        );
    }

    // ── CPU-only path ─────────────────────────────────────────────────────────
    if total_ram_gb < 3.5 {
        return (
            LlmTier::Qwen35_2B,
            format!(
                "Limited RAM ({:.0}GB) — Qwen 3.5 2B is the smallest multimodal option",
                total_ram_gb
            ),
        );
    }
    if total_ram_gb < 4.0 {
        return (
            LlmTier::LowAlt,
            format!(
                "Limited RAM ({:.0}GB) — Phi 4 Mini is a fast text-only fallback",
                total_ram_gb
            ),
        );
    }
    if total_ram_gb < 6.0 {
        return (
            LlmTier::Qwen35_4B,
            format!(
                "{:.0}GB RAM — Qwen 3.5 4B recommended for CPU inference",
                total_ram_gb
            ),
        );
    }
    if total_ram_gb < 8.0 {
        return (
            LlmTier::Qwen35_4B,
            format!(
                "{:.0}GB RAM — Qwen 3.5 4B recommended; CPU inference is slower but functional",
                total_ram_gb
            ),
        );
    }
    if total_ram_gb < 12.0 {
        return (
            LlmTier::Qwen35_9B,
            format!(
                "{:.0}GB RAM — Qwen 3.5 9B recommended (CPU inference; expect ~3–8 tokens/sec)",
                total_ram_gb
            ),
        );
    }

    (
        LlmTier::Qwen35_9B,
        format!(
            "{:.0}GB RAM — Qwen 3.5 9B recommended for balanced CPU performance",
            total_ram_gb
        ),
    )
}

fn compute_gpu_layers(hw: &HardwareProfile, gpu_vram_gb: f32) -> i32 {
    compute_gpu_layers_for_hw(hw, gpu_vram_gb)
}

/// Public wrapper so `lib.rs` auto-start can call it without going through
/// the full `select_llm_model` flow.
pub fn compute_gpu_layers_for_hw(hw: &HardwareProfile, gpu_vram_gb: f32) -> i32 {
    if hw.is_apple_silicon {
        return 99; // Metal — unified memory, offload all layers
    }
    if hw.supports_cuda || hw.supports_vulkan {
        if gpu_vram_gb >= 5.0 {
            return 99; // Full GPU offload
        }
        if gpu_vram_gb >= 2.0 {
            // Partial offload — heuristic: ~1 layer per 300MB VRAM, capped at 33
            let layers = ((gpu_vram_gb * 1024.0 - 512.0) / 300.0) as i32;
            return layers.max(1).min(33);
        }
        return 1;
    }
    0 // CPU only
}

fn can_upgrade_tier(
    tier: &LlmTier,
    total_ram_gb: f32,
    gpu_vram_gb: f32,
    is_apple_silicon: bool,
) -> bool {
    match tier {
        LlmTier::LowAlt | LlmTier::Qwen35_2B | LlmTier::Phi4MiniReasoning => {
            total_ram_gb >= 4.0
        }
        LlmTier::UltraLow
        | LlmTier::Qwen35_4B
        | LlmTier::Gemma3nE2B => total_ram_gb >= 6.0 || gpu_vram_gb >= 4.0,
        LlmTier::Low | LlmTier::Low2 | LlmTier::Low3 => {
            total_ram_gb >= 6.5 || gpu_vram_gb >= 4.0
        }
        LlmTier::Default | LlmTier::Qwen35_9B | LlmTier::GlmZ1_9B => {
            total_ram_gb >= 10.0 || gpu_vram_gb >= 8.0
        }
        LlmTier::Mid | LlmTier::Mid2 | LlmTier::Gemma4_12B => {
            total_ram_gb >= 16.0 || gpu_vram_gb >= 12.0 || (is_apple_silicon && total_ram_gb >= 16.0)
        }
        LlmTier::High | LlmTier::HighAlt | LlmTier::Devstral24B => {
            total_ram_gb >= 24.0 || gpu_vram_gb >= 16.0 || (is_apple_silicon && total_ram_gb >= 24.0)
        }
        LlmTier::High2
        | LlmTier::High3
        | LlmTier::High4
        | LlmTier::Coder30B
        | LlmTier::Glm47Flash => {
            total_ram_gb >= 36.0 || gpu_vram_gb >= 36.0 || (is_apple_silicon && total_ram_gb >= 36.0)
        }
        LlmTier::VHigh => false, // top consumer tier
        // Uncensored and server-grade don't participate in auto-upgrade
        _ => false,
    }
}

pub fn get_model_info(tier: &LlmTier) -> &'static LlmModelInfo {
    LLM_MODELS
        .iter()
        .find(|m| m.tier == *tier)
        .expect("all auto-selectable tiers have model info")
}
