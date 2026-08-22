/** Dense stat table with click-to-sort headers. Flat by design — glass behind
 *  small numerals costs the legibility this view exists to provide. */
import { useMemo, useState } from "react";

export interface Column<T> {
  key: string;
  label: string;
  value: (row: T) => number | string | null | undefined;
  render?: (row: T) => React.ReactNode;
  numeric?: boolean;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  initialSort?: string;
  initialDesc?: boolean;
  rowKey: (row: T) => string | number;
}

export function SortableTable<T>({
  rows,
  columns,
  initialSort,
  initialDesc = true,
  rowKey,
}: Props<T>) {
  const [sortKey, setSortKey] = useState(initialSort ?? columns[0].key);
  const [desc, setDesc] = useState(initialDesc);

  const sorted = useMemo(() => {
    const col = columns.find((c) => c.key === sortKey);
    if (!col) return rows;
    return [...rows].sort((a, b) => {
      const av = col.value(a);
      const bv = col.value(b);
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "string" || typeof bv === "string") {
        return desc
          ? String(bv).localeCompare(String(av))
          : String(av).localeCompare(String(bv));
      }
      return desc ? bv - av : av - bv;
    });
  }, [rows, columns, sortKey, desc]);

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="rank" />
            {columns.map((c, i) => (
              <th
                key={c.key}
                className={`${i === 0 ? "label " : ""}${sortKey === c.key ? "sorted" : ""}`}
                onClick={() => {
                  if (sortKey === c.key) setDesc(!desc);
                  else {
                    setSortKey(c.key);
                    setDesc(true);
                  }
                }}
                aria-sort={sortKey === c.key ? (desc ? "descending" : "ascending") : "none"}
              >
                {c.label}
                {sortKey === c.key ? (desc ? " ↓" : " ↑") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr key={rowKey(row)}>
              <td className="rank">{i + 1}</td>
              {columns.map((c, ci) => (
                <td key={c.key} className={ci === 0 ? "label" : undefined}>
                  {c.render ? c.render(row) : (c.value(row) ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
