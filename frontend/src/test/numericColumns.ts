// Numbers are right-aligned (td.num); their column's header must be too (th.num), or on a
// wide screen the title drifts far from its values. Checked after every test.

function columns(row: HTMLTableRowElement): HTMLTableCellElement[] {
  const out: HTMLTableCellElement[] = [];
  for (const cell of Array.from(row.cells)) {
    for (let k = 0; k < cell.colSpan; k++) out.push(cell);
  }
  return out;
}

/** Headers of numeric columns that aren't right-aligned, as their text. */
export function misalignedHeaders(root: ParentNode): string[] {
  const problems: string[] = [];
  for (const table of Array.from(root.querySelectorAll("table"))) {
    const head = table.tHead?.rows[table.tHead.rows.length - 1];
    if (!head) continue;
    const headers = columns(head);
    const numeric = new Set<number>();
    for (const body of Array.from(table.tBodies)) {
      for (const row of Array.from(body.rows)) {
        columns(row).forEach((cell, i) => {
          if (cell.tagName === "TD" && cell.colSpan === 1 && cell.classList.contains("num")) numeric.add(i);
        });
      }
    }
    for (const i of numeric) {
      const th = headers[i];
      if (th && !th.classList.contains("num")) problems.push(th.textContent || `column ${i + 1}`);
    }
  }
  return problems;
}
