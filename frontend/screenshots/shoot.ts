// Screenshots of the built UI against the invented demo cluster (D59).
// Usage (from frontend/, after `npm run build`):
//   node screenshots/shoot.ts [out-dir] [--light] [--only=overview,member]
// Dark by default (the README's theme); --light writes *-light.png.
// Uses the installed Google Chrome (or $CHROME_PATH); nothing reaches a real LXD.
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium, type Page, type Route } from "playwright-core";
import { preview } from "vite";
import { NOW, answer } from "./demo.ts";

const args = process.argv.slice(2);
const LIGHT = args.includes("--light");
const ONLY = args.find((a) => a.startsWith("--only="))?.slice(7).split(",");
const OUT = resolve(args.find((a) => !a.startsWith("--")) ?? "../docs/screenshots");

export const PAGES: { file: string; path: string; wait?: string; full?: boolean; prepare?: (p: Page) => Promise<void> }[] = [
  { file: "overview.png", path: "/?range=86400", wait: "Hosts", full: true },
  { file: "instances.png", path: "/instances", wait: "db-primary" },
  { file: "instance.png", path: "/instances/web/db-primary?range=86400", wait: "CPU (% of its CPUs)", full: true },
  {
    file: "create-instance.png",
    path: "/instances/new",
    wait: "noble",
    full: true,
    prepare: async (p) => {
      await p.getByLabel("Name", { exact: true }).fill("web-3");
      await p.getByRole("button", { name: "Use" }).first().click();
    },
  },
  { file: "member.png", path: "/members/node-2?range=86400", wait: "Host (node_exporter)", full: true },
  { file: "storage.png", path: "/storage?range=86400", wait: "remote", full: true },
  { file: "ceph.png", path: "/ceph?range=86400", wait: "HEALTH_OK", full: true },
  { file: "networks.png", path: "/networks?range=86400", wait: "UPLINK", full: true },
  { file: "ovn.png", path: "/ovn", wait: "UPLINK", full: true },
  // LXD 6 (MicroCloud 3.4) only
  { file: "placement-groups.png", path: "/placement-groups", wait: "infra-spread" },
  { file: "load-balancers.png", path: "/networks/default/default", wait: "198.51.100.10", full: true },
];

async function main() {
  const server = await preview({ preview: { port: 4174, strictPort: false }, logLevel: "warn" });
  const base = server.resolvedUrls?.local[0];
  if (!base) throw new Error("vite preview did not start");
  const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" });
  const missing = new Set<string>();
  try {
    const ctx = await browser.newContext({ viewport: { width: 1680, height: 1000 }, colorScheme: LIGHT ? "light" : "dark", timezoneId: "UTC", locale: "en-GB" });
    await ctx.addInitScript(`{ const t = ${NOW * 1000}; Date.now = () => t; }`);
    await ctx.route("**/api/**", (route: Route) => {
      const req = route.request();
      const body = answer(req.method(), new URL(req.url()));
      if (body === undefined) {
        missing.add(new URL(req.url()).pathname);
        return route.fulfill({ status: 404, json: { detail: "not in the demo" } });
      }
      return route.fulfill({ json: body });
    });
    await mkdir(OUT, { recursive: true });
    for (const page of PAGES.filter((p) => !ONLY || ONLY.includes(p.file.replace(/\.png$/, "")))) {
      const tab = await ctx.newPage();
      await tab.goto(base.replace(/\/$/, "") + page.path, { waitUntil: "networkidle" });
      if (page.wait) await tab.getByText(page.wait).first().waitFor();
      if (page.prepare) await page.prepare(tab);
      await tab.waitForTimeout(800); // charts draw after a resize tick
      const file = LIGHT ? page.file.replace(/\.png$/, "-light.png") : page.file;
      await tab.screenshot({ path: resolve(OUT, file), fullPage: page.full ?? false });
      console.log(`wrote ${file}`);
      await tab.close();
    }
  } finally {
    await browser.close();
    await new Promise<void>((done) => server.httpServer.close(() => done()));
  }
  if (missing.size) console.warn(`not in the demo (404): ${[...missing].join(", ")}`);
}

if (import.meta.url === `file://${process.argv[1]}`) await main();
