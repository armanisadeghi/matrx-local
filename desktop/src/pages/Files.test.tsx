import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Files } from "./Files";

describe("Files page", () => {
  it("is a nonblocking host-files surface while the engine is disconnected", () => {
    const html = renderToStaticMarkup(<Files engineStatus="disconnected" />);
    expect(html).toContain("This Device");
    expect(html).toContain("Connect to the engine");
    expect(html).toContain("Search names and paths");
  });
});
