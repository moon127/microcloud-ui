import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import Peers from "./Peers";

const ok = { operation: null, metadata: null };
const dialog = () => within(screen.getByRole("dialog"));
// as seen live on LXD 6.10 (Phase 10a)
const peer = (over: Partial<api.NetworkPeer> = {}): api.NetworkPeer => ({
  name: "to-ovn2",
  description: "test",
  target_project: "default",
  target_network: "ovn2",
  status: "Created",
  config: {},
  used_by: [],
  ...over,
});
const ovn = (name: string): api.Network => ({ name, type: "ovn", managed: true, description: "", status: "Created", config: {} });

beforeEach(() => {
  vi.spyOn(api, "fetchProjects").mockResolvedValue([
    { name: "default", description: "", config: {}, used_by: [] },
    { name: "web", description: "", config: { restricted: "true" }, used_by: [] },
  ]);
  vi.spyOn(api, "fetchNetworks").mockImplementation((project) =>
    Promise.resolve(project === "default" ? [ovn("ovn1"), ovn("ovn2"), { ...ovn("br0"), type: "bridge" }] : [ovn("web-net")]),
  );
});
afterEach(() => vi.restoreAllMocks());

describe("Peers", () => {
  it("lists peers, with Pending explained, and edits and deletes them", async () => {
    vi.spyOn(api, "fetchPeers").mockResolvedValue([peer(), peer({ name: "to-web", target_project: "web", target_network: "web-net", status: "Pending", description: "" })]);
    const upd = vi.spyOn(api, "updatePeer").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deletePeer").mockResolvedValue(ok);
    renderAt(<Peers project="default" network="ovn1" admin />);
    const row = (await screen.findByText("to-ovn2")).closest("tr")!;
    expect(within(row).getByRole("link", { name: "default/ovn2" })).toHaveAttribute("href", "/networks/default/ovn2");
    expect(screen.getByText("to-web").closest("tr")).toHaveTextContent("Pending: the other network hasn't peered back");
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    fireEvent.change(dialog().getByLabelText("Description"), { target: { value: "east-west" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(upd).toHaveBeenCalledWith("default", "ovn1", "to-ovn2", { description: "east-west", config: {} });
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    expect(dialog().getByText(/stays until it's deleted there too/)).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "ovn1", "to-ovn2");
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("adds a peer and peers back from the other network", async () => {
    vi.spyOn(api, "fetchPeers").mockResolvedValue([]);
    const create = vi.spyOn(api, "createPeer").mockResolvedValue(ok);
    renderAt(<Peers project="default" network="ovn1" admin />);
    expect(await screen.findByText("None.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add peer" }));
    // this network itself and non-OVN networks aren't offered
    await waitFor(() => expect(within(dialog().getByLabelText("Network")).getAllByRole("option").map((o) => o.textContent)).toEqual(["ovn2"]));
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
    expect(create).toHaveBeenNthCalledWith(1, "default", "ovn1", { name: "to-ovn2", description: "", target_project: "default", target_network: "ovn2" });
    expect(create).toHaveBeenNthCalledWith(2, "default", "ovn2", { name: "to-ovn1", description: "", target_project: "default", target_network: "ovn1" });
    await waitFor(() => expect(screen.getByRole("list", { name: "Actions" })).toHaveTextContent("✓ Peer ovn1 with default/ovn2"));
  });

  it("one side only when the user can't manage the other project, or chooses so", async () => {
    vi.spyOn(api, "fetchPeers").mockResolvedValue([]);
    const create = vi.spyOn(api, "createPeer").mockResolvedValue(ok);
    renderAt(<Peers project="default" network="ovn1" admin />, {
      s: session({ user: { username: "p", role: "project", grants: { default: "admin", web: "viewer" } } }),
    });
    fireEvent.click(await screen.findByRole("button", { name: "Add peer" }));
    await waitFor(() => expect(dialog().getAllByRole("option").map((o) => o.textContent)).toContain("web"));
    fireEvent.change(dialog().getByLabelText("Project"), { target: { value: "web" } });
    await waitFor(() => expect(dialog().getByLabelText("Network")).toHaveValue("web-net"));
    expect(dialog().getByText(/Someone who can manage web has to peer back/)).toBeInTheDocument();
    fireEvent.change(dialog().getByLabelText(/^Name/), { target: { value: "-bad" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent(/Names start/);
    fireEvent.change(dialog().getByLabelText(/^Name/), { target: { value: "east" } });
    fireEvent.change(dialog().getByLabelText("Description"), { target: { value: "d" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith("default", "ovn1", { name: "east", description: "d", target_project: "web", target_network: "web-net" });
  });

  it("unticking 'peer back' makes one side; no network to choose; read-only; errors", async () => {
    vi.spyOn(api, "fetchPeers").mockResolvedValue([]);
    const create = vi.spyOn(api, "createPeer").mockResolvedValue(ok);
    const a = renderAt(<Peers project="default" network="ovn1" admin />);
    fireEvent.click(await screen.findByRole("button", { name: "Add peer" }));
    await waitFor(() => expect(dialog().getByLabelText("Network")).toHaveValue("ovn2"));
    fireEvent.click(dialog().getByLabelText(/Also peer back/));
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.getByRole("list", { name: "Actions" })).toHaveTextContent("✓"));
    expect(create).toHaveBeenCalledTimes(1);
    a.unmount();
    vi.spyOn(api, "fetchNetworks").mockResolvedValue([ovn("ovn1")]);
    const b = renderAt(<Peers project="default" network="ovn1" admin />);
    fireEvent.click(await screen.findByRole("button", { name: "Add peer" }));
    expect(await dialog().findByText("No other OVN network in default.")).toBeInTheDocument();
    fireEvent.click(dialog().getByRole("button", { name: "Add" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Choose a network to peer with.");
    b.unmount();
    vi.spyOn(api, "fetchPeers").mockResolvedValue([peer()]);
    const c = renderAt(<Peers project="default" network="ovn1" admin={false} />);
    await screen.findByText("to-ovn2");
    expect(screen.queryByRole("button", { name: "Add peer" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    c.unmount();
    vi.spyOn(api, "fetchPeers").mockRejectedValue(new Error("boom"));
    renderAt(<Peers project="default" network="ovn1" admin />);
    expect(await screen.findByText("Peers unavailable: boom")).toBeInTheDocument();
  });
});
