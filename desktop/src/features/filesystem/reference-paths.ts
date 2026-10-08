/** The draft line a filesystem result's "reference" action hands the person (one wording, every chat). */
export function referencePathsText(paths: readonly string[]): string {
  return paths.length === 1
    ? `Use this local path: ${paths[0]}`
    : `Use these local paths:\n${paths.map((path) => `- ${path}`).join("\n")}`;
}
