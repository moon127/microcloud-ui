import { fetchNetworkTraffic, type Series } from "../api";
import MetricPanel from "../chart/MetricPanel";
import { nonZero, refreshFor, type PanelQuery } from "../chart/panel";
import TimeChart from "../chart/TimeChart";
import { formatRate } from "../format";
import { useRange } from "../range";
import RangePicker from "../RangePicker";
import { useApi } from "../useApi";

const label = (suffix: string) => (s: Series) => ({ label: `${s.labels.network} ${suffix}`, points: s.points });

export default function NetworksPage() {
  const range = useRange();
  const traffic = useApi(`net-traffic:${range}`, () => fetchNetworkTraffic(range), refreshFor(range));
  const host: PanelQuery[] = [
    {
      name: "mcui_host_network_receive_bytes_total",
      fn: "rate",
      label: (l) => `${l.member} ${l.network} rx`,
    },
    {
      name: "mcui_host_network_transmit_bytes_total",
      fn: "rate",
      label: (l) => `${l.member} ${l.network} tx`,
    },
  ];
  return (
    <>
      <h1>Networks</h1>
      <RangePicker />
      <h2>LXD networks (from instance NICs)</h2>
      <p className="muted">
        OVN networks don't report their own counters, so traffic is the sum of the instances' NICs on each network.
      </p>
      {traffic.error && <p role="alert">{traffic.error}</p>}
      {traffic.data && (
        <div className="grid">
          <TimeChart
            title="Received by instances"
            format={formatRate}
            series={traffic.data.rx.map(label("rx"))}
          />
          <TimeChart
            title="Sent by instances"
            format={formatRate}
            series={traffic.data.tx.map(label("tx"))}
          />
        </div>
      )}
      <h2>Host interfaces</h2>
      <div className="grid">
        <MetricPanel title="Host interface traffic" range={range} format={formatRate} queries={host} compute={nonZero(host)} />
      </div>
    </>
  );
}
