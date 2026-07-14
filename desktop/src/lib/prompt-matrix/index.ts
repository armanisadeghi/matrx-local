/**
 * prompt-matrix — {{variable}} templates → planned, countable, ordered batches.
 *
 * The engine is media-agnostic: it knows variables, options, strategies and
 * combinations, nothing else. Consumers bind it to a generator with a
 * MatrixTarget (see ./targets). `createImageTarget` is the first one; video
 * and text-prompt targets slot in beside it without touching the engine.
 *
 *   const spec  = { fields, variables, pools, strategy, seed };
 *   const plan  = expandMatrix(spec);          // ordered runs + exact total
 *   const built = buildJobs(target, base, plan.combinations, spec.variables);
 */

export {
  LARGE_BATCH_THRESHOLD,
  MAX_BATCH_SIZE,
  MAX_MATERIALIZED,
  type MatrixCombination,
  type MatrixOption,
  type MatrixPlan,
  type MatrixPool,
  type MatrixSpec,
  type MatrixStrategy,
  type MatrixVariable,
  type PoolAssign,
  type SeedMode,
  type SeedPolicy,
  type StrategyKind,
  type TemplateField,
  type VariableBinding,
} from "./types";

export {
  extractPoolRefs,
  extractVariableNames,
  findTokens,
  hasTokens,
  hasUnclosedToken,
  normalizeName,
  poolSlotName,
  renderTemplate,
  sortSlots,
  tidyPrompt,
  variableKey,
  type PoolRef,
  type TokenMatch,
} from "./parse";

export { MAX_SEED, randomSeed, Rng, sampleIndices } from "./rng";

export {
  countPlan,
  expandMatrix,
  validateSpec,
  type PlanValidation,
} from "./expand";

export {
  buildJobs,
  syncPoolsWithTokens,
  syncVariablesWithTokens,
  type BuiltJob,
  type BuildJobsResult,
  type MatrixTarget,
  type ParamAxis,
  type ParseResult,
} from "./targets";

export {
  createImageTarget,
  IMAGE_TEMPLATE_FIELDS,
  type ImageTargetContext,
} from "./imageTarget";

export {
  MATRIX_EXPORT_VERSION,
  downloadMatrixExport,
  matrixExportFilename,
  parseMatrixImport,
  serializeMatrixExport,
  type MatrixExportFile,
  type MatrixImportResult,
} from "./io";

export {
  isLibraryEntry,
  libraryEntryFromPool,
  libraryEntryFromVariable,
  poolFromLibraryEntry,
  sanitizeLibraryEntries,
  variableFromLibraryEntry,
  type LibraryEntry,
  type LibraryEntryKind,
} from "./library";

export { insertLibraryEntryInSpec, renameVariableInSpec } from "./edit";
