import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../../api";
import { renderAt, session } from "../../test/render";
import AclPage from "./AclPage";
import AclsPage from "./AclsPage";
import NetworkPage from "./NetworkPage";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem("mcui.project");
});

const ok: api.Started = { operation: null, metadata: null };
const viewer = () => session({ user: { username: "v", role: "viewer" } });
const dialog = () => within(screen.getByRole("dialog"));

function routes(url: string, s = session()) {
  return renderAt(
    <Routes>
      <Route path="/network-acls" element={<AclsPage />} />
      <Route path="/network-acls/:project/:name" element={<AclPage />} />
      <Route path="/networks/:project/:name" element={<NetworkPage />} />
    </Routes>,
    { url, s },
  );
}

const acl = (over: Partial<api.NetworkAcl> = {}): api.NetworkAcl => ({
  name: "web",
  description: "web servers",
  ingress: [{ action: "allow", protocol: "tcp", destination_port: "80,443" } as api.AclRule],
  egress: null,
  config: {},
  used_by: ["/1.0/networks/br0", "/1.0/network-acls/other"],
  ...over,
});

describe("AclsPage", () => {
  it("lists ACLs and creates one", async () => {
    localStorage.setItem("mcui.project", "lab");
    vi.spyOn(api, "fetchAcls").mockResolvedValue([acl(), acl({ name: "empty", ingress: null, used_by: null })]);
    vi.spyOn(api, "fetchAcl").mockResolvedValue(acl({ name: "new", ingress: [], used_by: [] }));
    const create = vi.spyOn(api, "createAcl").mockResolvedValue(ok);
    routes("/network-acls");
    const row = (await screen.findByRole("link", { name: "web" })).closest("tr")!;
    expect(row).toHaveTextContent("web servers");
    expect([...row.querySelectorAll("td.num")].map((td) => td.textContent)).toEqual(["1", "0", "2"]);
    expect(screen.getByRole("link", { name: "web" })).toHaveAttribute("href", "/network-acls/lab/web");
    fireEvent.click(screen.getByRole("button", { name: "Create ACL" }));
    fireEvent.change(dialog().getByLabelText("ACL name"), { target: { value: "new" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(create).toHaveBeenCalledWith("lab", "new", { description: "", ingress: [], egress: [], config: {} });
    expect(await screen.findByRole("heading", { name: "ACL new in lab" })).toBeInTheDocument();
    expect(await screen.findAllByText("No rules: the network's default action applies.")).toHaveLength(2);
  });

  it("viewer, empty, cancel and error states", async () => {
    vi.spyOn(api, "fetchAcls").mockResolvedValue([]);
    const a = routes("/network-acls", viewer());
    expect(await screen.findByText("No ACLs.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create ACL" })).toBeNull();
    a.unmount();
    const b = routes("/network-acls");
    fireEvent.click(await screen.findByRole("button", { name: "Create ACL" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    b.unmount();
    vi.spyOn(api, "fetchAcls").mockRejectedValue(new Error("down"));
    routes("/network-acls");
    expect(await screen.findByRole("alert")).toHaveTextContent("down");
  });
});

describe("AclPage", () => {
  it("edits rules, renames and deletes", async () => {
    vi.spyOn(api, "fetchAcl").mockResolvedValue(acl());
    vi.spyOn(api, "fetchAcls").mockResolvedValue([]);
    const put = vi.spyOn(api, "updateAcl").mockResolvedValue(ok);
    const ren = vi.spyOn(api, "renameAcl").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteAcl").mockResolvedValue(ok);
    routes("/network-acls/default/web");
    expect(await screen.findByLabelText("Ingress rule 1 destination port")).toHaveValue("80,443");
    expect(screen.getByRole("link", { name: "network br0" })).toHaveAttribute("href", "/networks/default/br0");
    expect(screen.getByRole("link", { name: "network-acl other" })).toHaveAttribute("href", "/network-acls/default/other");
    // icmp fields are off for tcp; switching to icmp4 clears the ports
    expect(screen.getByLabelText("Ingress rule 1 icmp type")).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Ingress rule 1 protocol"), { target: { value: "icmp4" } });
    expect(screen.getByLabelText("Ingress rule 1 destination port")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Ingress rule 1 icmp type"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("Ingress rule 1 protocol"), { target: { value: "udp" } });
    expect(screen.getByLabelText("Ingress rule 1 icmp type")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Ingress rule 1 protocol"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Ingress rule 1 action"), { target: { value: "reject" } });
    fireEvent.change(screen.getByLabelText("Ingress rule 1 state"), { target: { value: "logged" } });
    fireEvent.change(screen.getByLabelText("Ingress rule 1 source"), { target: { value: "@external" } });
    fireEvent.click(screen.getByRole("button", { name: "Add egress rule" }));
    fireEvent.change(screen.getByLabelText("Egress rule 1 action"), { target: { value: "drop" } });
    fireEvent.click(screen.getByRole("button", { name: "Add egress rule" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Egress rule 2" }));
    fireEvent.change(screen.getByLabelText("Description"), { target: { value: "edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    const blank = { description: "", source: "", destination: "", protocol: "", source_port: "", destination_port: "", icmp_type: "", icmp_code: "" };
    expect(put).toHaveBeenCalledWith("default", "web", {
      description: "edited",
      ingress: [{ ...blank, action: "reject", state: "logged", source: "@external" }],
      egress: [{ ...blank, action: "drop", state: "enabled" }],
      config: {},
    });

    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.change(dialog().getByLabelText("New name"), { target: { value: "web2" } });
    fireEvent.click(dialog().getByRole("button", { name: "OK" }));
    expect(ren).toHaveBeenCalledWith("default", "web", "web2");
    await screen.findByRole("heading", { name: "ACL web2 in default" });

    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "web2");
    expect(await screen.findByRole("heading", { name: "Network ACLs in default" })).toBeInTheDocument();
  });

  it("is read-only for viewers; shows errors", async () => {
    vi.spyOn(api, "fetchAcl").mockResolvedValue(acl({ used_by: null, config: undefined as never }));
    const a = routes("/network-acls/default/web", viewer());
    expect(await screen.findByLabelText("Ingress rule 1 source")).toHaveAttribute("readonly");
    expect(screen.getByLabelText("Ingress rule 1 action")).toBeDisabled();
    expect(screen.queryByRole("toolbar")).toBeNull();
    expect(screen.queryByRole("button", { name: /Add/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(screen.getByText("Nothing.")).toBeInTheDocument();
    a.unmount();
    vi.spyOn(api, "fetchAcl").mockRejectedValue(new Error("Network ACL not found"));
    routes("/network-acls/default/gone");
    expect(await screen.findByRole("alert")).toHaveTextContent("Network ACL not found");
  });
});

describe("network page: ACLs and forwards", () => {
  const network = (over: Partial<api.NetworkDetail> = {}): api.NetworkDetail => ({
    name: "br0",
    type: "bridge",
    managed: true,
    description: "",
    status: "Created",
    config: { "security.acls": "web,gone", "security.acls.default.ingress.action": "drop" },
    used_by: [],
    locations: ["m1", "m2"],
    ...over,
  });
  const fwd: api.NetworkForward = {
    listen_address: "192.0.2.10",
    description: "web",
    config: { target_address: "10.0.0.6" },
    ports: [
      { protocol: "tcp", listen_port: "80", target_address: "10.0.0.5", target_port: "8080", description: "" },
      { protocol: "udp", listen_port: "53", target_address: "10.0.0.7", target_port: "", description: "" },
    ],
    location: "m1",
  };

  beforeEach(() => {
    vi.spyOn(api, "fetchNetworkState").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "fetchNetworkMembers").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkAllocations").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetworkLeases").mockResolvedValue([]);
    vi.spyOn(api, "fetchAcls").mockResolvedValue([acl(), acl({ name: "ssh" })]);
  });

  it("assigns ACLs and default actions through the network's config", async () => {
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(network());
    vi.spyOn(api, "fetchForwards").mockResolvedValue([]);
    const put = vi.spyOn(api, "updateNetwork").mockResolvedValue(ok);
    routes("/networks/default/br0");
    const acls = within(await screen.findByRole("region", { name: "ACLs" }));
    expect(await acls.findByRole("checkbox", { name: "ssh" })).not.toBeChecked();
    expect(acls.getByRole("checkbox", { name: "gone" })).toBeChecked(); // assigned but no longer listed
    expect(acls.getByRole("link", { name: "web" })).toHaveAttribute("href", "/network-acls/default/web");
    fireEvent.click(acls.getByRole("checkbox", { name: "ssh" }));
    fireEvent.click(acls.getByRole("checkbox", { name: "gone" }));
    fireEvent.change(acls.getByLabelText("Default ingress action"), { target: { value: "" } });
    fireEvent.change(acls.getByLabelText("Default egress action"), { target: { value: "allow" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith("default", "br0", {
      description: "",
      config: { "security.acls": "web,ssh", "security.acls.default.egress.action": "allow" },
    });
    fireEvent.click(acls.getByRole("checkbox", { name: "web" }));
    fireEvent.click(acls.getByRole("checkbox", { name: "ssh" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenLastCalledWith("default", "br0", { description: "", config: { "security.acls.default.egress.action": "allow" } });
  });

  it("no ACLs, ACL errors, viewers, and physical networks have neither section", async () => {
    vi.spyOn(api, "fetchAcls").mockResolvedValue([]);
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(network({ type: "ovn", config: {} }));
    vi.spyOn(api, "fetchForwards").mockRejectedValue(new Error("not supported"));
    const a = routes("/networks/default/o1", viewer());
    expect(await screen.findByText(/No ACLs in default/)).toBeInTheDocument();
    expect(screen.queryByText("Save the network to apply.")).toBeNull();
    expect(await screen.findByText("Forwards unavailable: not supported")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add forward" })).toBeNull();
    a.unmount();
    vi.spyOn(api, "fetchAcls").mockRejectedValue(new Error("acl down"));
    const b = routes("/networks/default/o1");
    expect(await screen.findByText("acl down")).toBeInTheDocument();
    b.unmount();
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(network({ type: "physical", config: {} }));
    routes("/networks/default/up");
    expect(await screen.findByRole("heading", { name: "State" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "ACLs" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Forwards" })).toBeNull();
  });

  it("adds, edits and deletes forwards", async () => {
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(network());
    vi.spyOn(api, "fetchForwards").mockResolvedValue([fwd, { ...fwd, listen_address: "192.0.2.11", config: {}, ports: null, location: "" }]);
    const create = vi.spyOn(api, "createForward").mockResolvedValue(ok);
    const put = vi.spyOn(api, "updateForward").mockResolvedValue(ok);
    const del = vi.spyOn(api, "deleteForward").mockResolvedValue(ok);
    routes("/networks/default/br0");
    const section = within(await screen.findByRole("region", { name: "Forwards" }));
    const row = (await section.findByText("192.0.2.10")).closest("tr")!;
    expect(row).toHaveTextContent("tcp 80 → 10.0.0.5:8080; udp 53 → 10.0.0.7");
    expect(row).toHaveTextContent("m1");

    // add: needs a listen address and complete ports
    fireEvent.click(section.getByRole("button", { name: "Add forward" }));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Enter the listen address");
    fireEvent.change(dialog().getByLabelText("Listen address"), { target: { value: " 192.0.2.20 " } });
    fireEvent.change(dialog().getByLabelText("Member (bridge networks in a cluster)"), { target: { value: "m2" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add port" }));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(dialog().getByRole("alert")).toHaveTextContent("Every port needs");
    fireEvent.change(dialog().getByLabelText("Protocol"), { target: { value: "udp" } });
    fireEvent.change(dialog().getByLabelText("Listen ports"), { target: { value: " 53 " } });
    fireEvent.change(dialog().getByLabelText("Target address"), { target: { value: "10.0.0.9" } });
    fireEvent.change(dialog().getByLabelText("Target ports"), { target: { value: "5353" } });
    fireEvent.click(dialog().getByRole("button", { name: "Add port" }));
    fireEvent.click(dialog().getByRole("button", { name: "Remove port 2" }));
    fireEvent.change(dialog().getByLabelText("Default target address (optional)"), { target: { value: "10.0.0.1" } });
    fireEvent.change(dialog().getByLabelText("Description"), { target: { value: " dns " } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(create).toHaveBeenCalledWith(
      "default",
      "br0",
      {
        listen_address: "192.0.2.20",
        description: "dns",
        config: { target_address: "10.0.0.1" },
        ports: [{ protocol: "udp", listen_port: "53", target_address: "10.0.0.9", target_port: "5353", description: "" }],
      },
      "m2",
    );

    // edit: listen address fixed, no member choice; clearing the default target drops it
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    expect(dialog().getByLabelText("Listen address")).toHaveAttribute("readonly");
    expect(dialog().queryByLabelText(/Member/)).toBeNull();
    fireEvent.change(dialog().getByLabelText("Default target address (optional)"), { target: { value: "" } });
    fireEvent.click(dialog().getByRole("button", { name: "Remove port 2" }));
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith(
      "default",
      "br0",
      "192.0.2.10",
      { description: "web", config: {}, ports: [fwd.ports![0]] },
      "m1",
    );
    fireEvent.click(within(row).getByRole("button", { name: "Edit" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));

    // delete, with and without a member
    const row2 = section.getByText("192.0.2.11").closest("tr")!;
    expect(row2).toHaveTextContent("–");
    fireEvent.click(within(row2).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(row2).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    expect(del).toHaveBeenCalledWith("default", "br0", "192.0.2.11", undefined);
    fireEvent.click(within(row).getByRole("button", { name: "Delete" }));
    fireEvent.click(dialog().getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(del).toHaveBeenLastCalledWith("default", "br0", "192.0.2.10", "m1"));
  });

  it("OVN forwards don't offer a member", async () => {
    vi.spyOn(api, "fetchNetwork").mockResolvedValue(network({ type: "ovn", config: {} }));
    vi.spyOn(api, "fetchForwards").mockResolvedValue([]);
    const create = vi.spyOn(api, "createForward").mockResolvedValue(ok);
    routes("/networks/default/o1");
    fireEvent.click(await screen.findByRole("button", { name: "Add forward" }));
    expect(dialog().queryByLabelText(/Member/)).toBeNull();
    fireEvent.change(dialog().getByLabelText("Listen address"), { target: { value: "192.0.2.30" } });
    fireEvent.click(dialog().getByRole("button", { name: "Save" }));
    expect(create).toHaveBeenCalledWith("default", "o1", { listen_address: "192.0.2.30", description: "", config: {}, ports: [] }, undefined);
  });
});
