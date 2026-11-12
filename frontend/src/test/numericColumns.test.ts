import { describe, expect, it } from "vitest";
import { misalignedHeaders } from "./numericColumns";

function table(html: string): HTMLElement {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
}

describe("misalignedHeaders", () => {
  it("names the header of a numeric column that isn't right-aligned", () => {
    const t = table(`<table><thead><tr><th>Name</th><th>Size</th><th class="num">Count</th></tr></thead>
      <tbody><tr><td>a</td><td class="num">1 GiB</td><td class="num">3</td></tr></tbody></table>`);
    expect(misalignedHeaders(t)).toEqual(["Size"]);
  });

  it("accepts aligned headers, tables without a header row, and spanning cells", () => {
    const t = table(`<table><thead><tr><th>Name</th><th class="num">Size</th><th>Mode</th></tr></thead>
      <tbody><tr><td colspan="2" class="num">–</td><td>x</td></tr>
      <tr><td>a</td><td class="num">1</td><td>rw</td></tr></tbody></table>
      <table><tbody><tr><td>a</td><td class="num">1</td></tr></tbody></table>`);
    expect(misalignedHeaders(t)).toEqual([]);
  });

  it("counts columns past a spanning header", () => {
    const t = table(`<table><thead><tr><th colspan="2">Name</th><th></th></tr></thead>
      <tbody><tr><td>a</td><td>b</td><td class="num">3</td></tr></tbody></table>`);
    expect(misalignedHeaders(t)).toEqual(["column 3"]);
  });
});
