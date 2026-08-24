/**
 * The Content IR scream seam (`ContentIrDiagnostics` in
 * docs/CONTENT_IR_CONSUMER_GUIDE.md § Minimum local boundaries).
 *
 * `@ai-matrx/content-ir-react` makes every recovery path loud and refuses to
 * own where the scream lands. A no-op here would be choosing silence, which
 * the guide names as its own defect ("Diagnostics are injectable").
 */

import type { ContentIrErrorReporter } from "@ai-matrx/content-ir-react";

export const reportContentIrError: ContentIrErrorReporter = (report) => {
  console.error(
    `[content-ir] ${report.message}`,
    report.relation ? { relation: report.relation, raw: report.raw } : (report.raw ?? ""),
  );
};
