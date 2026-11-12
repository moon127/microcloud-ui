import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { FakeSocket } from "./test/fakeSocket";
import { misalignedHeaders } from "./test/numericColumns";
import { plots } from "./test/uplotMock";

vi.mock("uplot", () => import("./test/uplotMock"));

// pages that listen to /api/events get a fake socket instead of a real connection
beforeEach(() => {
  FakeSocket.all = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  const misaligned = misalignedHeaders(document.body);
  cleanup();
  if (misaligned.length) {
    throw new Error(`numeric columns need <th className="num">: ${misaligned.join(", ")}`);
  }
  plots.length = 0;
});
