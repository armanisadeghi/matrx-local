/**
 * The prompt-matrix UI, built once in core/ so every layout variant composes it
 * rather than forking its own copy.
 *
 *   PromptMatrixPanel — template → variables → strategy → count → queue
 *   BatchQueuePanel   — pause / drag-reorder / cancel-batch / retry, live
 */
export { PromptMatrixPanel, PromptMatrixQueueBar } from "./PromptMatrixPanel";
export { BatchQueuePanel } from "./BatchQueuePanel";
export { BatchConfirmDialog, formatDuration } from "./BatchConfirmDialog";
export { TemplateEditor } from "./TemplateEditor";
export { VariableCard } from "./VariableCard";
export { StrategyControls } from "./StrategyControls";
