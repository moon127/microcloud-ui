import { fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { renderAt } from "../test/render";
import AuditPage from "./AuditPage";

afterEach(() => vi.restoreAllMocks());

const entry = (id: number, over: Partial<api.AuditEntry> = {}): api.AuditEntry => ({
  id,
  ts: 1_794_557_765 + id,
  username: "alice",
  action: "instance.start",
  target: `web/c${id}`,
  ok: true,
  detail: null,
  operation_id: `op${id}`,
  ...over,
});

describe("AuditPage", () => {
  it("lists changes, filters them and marks failures", async () => {
    vi.spyOn(api, "fetchAudit").mockResolvedValue([
      entry(3, { action: "user.grant", target: "pat", detail: { project: "web", role: "operator" }, operation_id: null }),
      entry(2, { ok: false, detail: { error: "Instance is already running" } }),
      entry(1, { username: null, action: "user.create", target: "bob", detail: { via: "cli" } }),
    ]);
    renderAt(<AuditPage />);
    expect(await screen.findByText("project=web, role=operator")).toBeInTheDocument();
    expect(screen.getByText("web/c2").closest("tr")).toHaveTextContent("failed");
    expect(screen.getByText("(command line)")).toBeInTheDocument();
    expect(screen.getByText("op2")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load older" })).toBeNull(); // under one page
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "ALREADY running" } });
    expect(screen.getAllByRole("row")).toHaveLength(2); // the header and one entry
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "nothing like it" } });
    expect(screen.getByText("Nothing matches.")).toBeInTheDocument();
  });

  it("pages back through older entries", async () => {
    const page = Array.from({ length: 100 }, (_, i) => entry(200 - i));
    const audit = vi
      .spyOn(api, "fetchAudit")
      .mockResolvedValueOnce(page)
      .mockResolvedValueOnce([entry(100), entry(99)])
      .mockRejectedValueOnce(new Error("LXD down"));
    renderAt(<AuditPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older" }));
    expect(await screen.findByText("web/c99")).toBeInTheDocument();
    expect(audit).toHaveBeenLastCalledWith(100, 101);
    expect(screen.queryByRole("button", { name: "Load older" })).toBeNull(); // a short page: the end
  });

  it("says when there's nothing, or an error", async () => {
    vi.spyOn(api, "fetchAudit").mockResolvedValue([]);
    const a = renderAt(<AuditPage />);
    expect(await screen.findByText("Nothing yet.")).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchAudit").mockRejectedValue(new Error("needs a session"));
    renderAt(<AuditPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("needs a session");
  });

  it("shows a failed page of older entries", async () => {
    vi.spyOn(api, "fetchAudit")
      .mockResolvedValueOnce(Array.from({ length: 100 }, (_, i) => entry(200 - i)))
      .mockRejectedValueOnce(new Error("LXD down"));
    renderAt(<AuditPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older" }));
    expect(await screen.findByText("LXD down")).toBeInTheDocument();
  });
});
