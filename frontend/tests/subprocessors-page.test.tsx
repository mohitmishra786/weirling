// @vitest-environment jsdom
import fs from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import SubprocessorsPage from "../src/app/subprocessors/page";

function getServicesFromDoc(): string[] {
  const docPath = path.resolve(__dirname, "../../docs/THIRD_PARTY_SERVICES.md");
  const content = fs.readFileSync(docPath, "utf-8");
  const lines = content.split("\n");
  const services: string[] = [];

  let inTable = false;
  for (const line of lines) {
    if (line.startsWith("| Service |")) {
      inTable = true;
      continue;
    }
    if (inTable) {
      if (line.startsWith("|---|")) continue;
      if (!line.startsWith("|")) break;
      const parts = line.split("|").map((p) => p.trim()).filter(Boolean);
      if (parts.length > 0) {
        // Strip bold markdown markers like **Groq** -> Groq
        const name = parts[0].replace(/\*\*/g, "").trim();
        if (name) services.push(name);
      }
    }
  }
  return services;
}

describe("Subprocessors page (#182)", () => {
  it("displays template legal notice", () => {
    render(<SubprocessorsPage />);
    expect(screen.getByText(/Template: not legal advice/i)).toBeInTheDocument();
  });

  it("ensures every service in docs/THIRD_PARTY_SERVICES.md appears on the page", () => {
    const services = getServicesFromDoc();
    expect(services.length).toBeGreaterThan(0);

    render(<SubprocessorsPage />);

    for (const service of services) {
      // Look for the main service name (e.g. "Groq", "OpenAI", "Resend")
      const baseName = service.split("(")[0].trim();
      const matches = screen.getAllByText((content) => content.includes(baseName));
      expect(
        matches.length,
        `Expected service "${baseName}" from docs/THIRD_PARTY_SERVICES.md to be rendered`
      ).toBeGreaterThan(0);
    }
  });

  it("renders columns for purpose, data sent, and processing location", () => {
    render(<SubprocessorsPage />);
    expect(screen.getByText("Purpose / Used for")).toBeInTheDocument();
    expect(screen.getByText("Data sent")).toBeInTheDocument();
    expect(screen.getByText("Location")).toBeInTheDocument();
  });
});
