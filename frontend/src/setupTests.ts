import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";
import { plots } from "./test/uplotMock";

vi.mock("uplot", () => import("./test/uplotMock"));

afterEach(() => {
  cleanup();
  plots.length = 0;
});
