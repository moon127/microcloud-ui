import { queryMetric, type QueryResult } from "../api";
import { useApi } from "../useApi";
import { defaultSeries, refreshFor, type PanelQuery } from "./panel";
import type { ChartSeries } from "./series";
import TimeChart from "./TimeChart";

interface Props {
  title: string;
  range: number;
  queries: PanelQuery[];
  format?: (v: number) => string;
  /** Turn query results into chart series; default: every series, labelled by ``label``. */
  compute?: (results: QueryResult[]) => ChartSeries[];
}

export default function MetricPanel({ title, range, queries, format, compute }: Props) {
  const key = JSON.stringify([range, queries.map((q) => [q.name, q.match, q.fn, q.by])]);
  const { data, error } = useApi(
    key,
    () => Promise.all(queries.map((q) => queryMetric({ ...q, range }))),
    refreshFor(range),
  );
  if (error) {
    return (
      <figure className="chart" aria-label={title}>
        <figcaption>{title}</figcaption>
        <p role="alert">{error}</p>
      </figure>
    );
  }
  if (!data) {
    return (
      <figure className="chart" aria-label={title}>
        <figcaption>{title}</figcaption>
        <p className="muted">Loading…</p>
      </figure>
    );
  }
  const series = compute ? compute(data) : defaultSeries(queries, data);
  return <TimeChart title={title} series={series} format={format} />;
}
