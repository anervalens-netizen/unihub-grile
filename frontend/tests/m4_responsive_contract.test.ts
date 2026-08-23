import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const layoutSource = readFileSync(join(process.cwd(), "src/components/Layout.tsx"), "utf8");
const responsiveSource = readFileSync(join(process.cwd(), "src/styles/responsive.css"), "utf8");

function mediaBlock(maxWidth: number, nextMaxWidth?: number): string {
  const startToken = `@media (max-width: ${maxWidth}px)`;
  const start = responsiveSource.indexOf(startToken);
  expect(start).toBeGreaterThanOrEqual(0);
  const end = nextMaxWidth
    ? responsiveSource.indexOf(`@media (max-width: ${nextMaxWidth}px)`, start + startToken.length)
    : responsiveSource.length;
  expect(end).toBeGreaterThan(start);
  return responsiveSource.slice(start, end);
}

describe("FE-013 responsive cascade contract", () => {
  it("loads the final responsive layer after the retail overrides", () => {
    const retailImport = layoutSource.indexOf('import "../styles/retail-overrides.css";');
    const responsiveImport = layoutSource.indexOf('import "../styles/responsive.css";');

    expect(retailImport).toBeGreaterThanOrEqual(0);
    expect(responsiveImport).toBeGreaterThan(retailImport);
  });

  it("keeps primary navigation available on mobile instead of hiding the sidebar", () => {
    const mobile = mediaBlock(760, 480);

    expect(mobile).toMatch(/\.app-sidebar\s*\{[\s\S]*?display:\s*block;/);
    expect(mobile).toMatch(/\.app-nav\s*\{[\s\S]*?overflow-x:\s*auto;/);
    expect(mobile).toMatch(/\.nav-link\s*\{[\s\S]*?min-width:\s*max-content;/);
  });

  it("collapses dense operational grids before tablet widths become cramped", () => {
    const tablet = mediaBlock(1000, 760);

    expect(tablet).toContain(".close-grid");
    expect(tablet).toContain("grid-template-columns: 1fr;");
    expect(tablet).toContain("grid-template-columns: repeat(3, minmax(0, 1fr));");
  });

  it("provides narrow-phone wrapping for long operational context and controls", () => {
    const phone = mediaBlock(480);

    expect(responsiveSource).toContain("overflow-wrap: anywhere;");
    expect(phone).toMatch(/\.exception-context \.context-pill\s*\{[\s\S]*?white-space:\s*normal;/);
    expect(phone).toMatch(/\.program-cell-editor-panel,[\s\S]*?grid-template-columns:\s*1fr;/);
    expect(phone).toMatch(/\.sync-status-row\s*\{[\s\S]*?flex-direction:\s*column;/);
  });
});
