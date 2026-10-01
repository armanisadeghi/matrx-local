/**
 * Schema types for the dynamic tool form system.
 * Each tool gets a ToolUISchema that describes its inputs and output format.
 */

/** Supported field types that map to specific UI components */
export type FieldType =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "select"
  | "file-path"
  | "tags"
  | "key-value"
  | "code"
  | "json";

/** Output display format */
export type OutputType =
  | "json"
  | "table"
  | "text"
  | "file-tree"
  | "image"
  | "log-stream";

/** Schema for a single form field */
export interface ToolFieldSchema {
  /** Unique key matching the tool's parameter name */
  name: string;
  /** Human-readable label */
  label: string;
  /** Field type determines which UI component renders */
  type: FieldType;
  /** Brief description shown as helper text */
  description?: string;
  /** Whether this field is required */
  required?: boolean;
  /** Default value */
  defaultValue?: unknown;
  /** Placeholder text */
  placeholder?: string;

  // Validation constraints
  /** Minimum value (number) or min length (text) */
  min?: number;
  /** Maximum value (number) or max length (text) */
  max?: number;
  /** Regex pattern for text validation */
  pattern?: string;
  /** Number must be a whole number (JSON Schema "integer") */
  integer?: boolean;
  /** Number must be strictly greater than this */
  exclusiveMin?: number;
  /** Number must be strictly less than this */
  exclusiveMax?: number;
  /** Element type for tags fields whose array holds numbers */
  itemType?: "string" | "number" | "integer";

  // Type-specific options
  /** Options for select-type fields */
  options?: Array<{ label: string; value: string }>;
  /** Language for code fields */
  codeLanguage?: string;
  /** File extension filters for file-path fields */
  fileExtensions?: string[];
  /** Allow picking directories (for file-path fields) */
  allowDirectory?: boolean;
}

/** Complete UI schema for a single tool */
export interface ToolUISchema {
  /** Tool name as registered in the engine (e.g., "Read", "Bash") */
  toolName: string;
  /** Human-readable display name */
  displayName: string;
  /** Brief description of what the tool does */
  description: string;
  /** Tool category for grouping */
  category: string;
  /** Lucide icon name */
  icon: string;
  /** Field definitions */
  fields: ToolFieldSchema[];
  /** How to render the output */
  outputType: OutputType;
  /** Optional example usage */
  examples?: Array<{
    label: string;
    values: Record<string, unknown>;
  }>;
}

/** Tool category metadata for display */
export interface ToolCategory {
  id: string;
  label: string;
  description: string;
  icon: string;
}
