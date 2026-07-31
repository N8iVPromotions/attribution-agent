import React, { useMemo, useRef, useState } from "react";
import { CHANNEL_ORDER, channelHex, channelLabel } from "../channels.js";
import { fmtMoney, fmtMonth } from "../api.js";
import { Legend } from "./ui.jsx";

const nice = (max) => {
  if (max <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(max)));
  const unit = max / pow;
  const step = unit <= 2 ? 2 : unit <= 5 ? 5 : 10;
  return step * pow;
};

function useTip() {
  const [tip, setTip] = useState(null);
  const wrapRef = useRef(null);
  const show = (evt, content) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setTip({ x: evt.clientX - rect.left, y: evt.clientY - rect.top, content });
  };
  return { tip, setTip, wrapRef, show };
}

function Tip({ tip }) {
  if (!tip) return null;
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }}>
      {tip.content}
    </div>
  );
}

// Monthly agency revenue, stacked by channel. 2px surface gaps between
// segments; rounded cap only on the top segment; per-segment hover tooltip.
export function StackedBars({ monthly, height = 240 }) {
  const { tip, setTip, wrapRef, show } = useTip();
  const W = 640;
  const pad = { l: 54, r: 10, t: 14, b: 26 };

  const { bars, yMax, activeChannels } = useMemo(() => {
    const active = CHANNEL_ORDER.filter((ch) =>
      monthly.some((m) => (m.by_channel?.[ch]?.revenue || 0) > 0)
    );
    const max = nice(Math.max(...monthly.map((m) => m.revenue)));
    return { bars: monthly, yMax: max, activeChannels: active };
  }, [monthly]);

  const innerW = W - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const slot = innerW / bars.length;
  const barW = Math.min(46, slot * 0.52);
  const y = (v) => pad.t + innerH * (1 - v / yMax);

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        role="img"
        aria-label="Monthly attributed revenue by channel"
      >
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <g key={f}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(yMax * f)} y2={y(yMax * f)} />
            <text className="axis-label" x={pad.l - 8} y={y(yMax * f) + 4} textAnchor="end">
              {fmtMoney(yMax * f)}
            </text>
          </g>
        ))}
        <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} stroke="var(--border-strong)" />

        {bars.map((m, i) => {
          const cx = pad.l + slot * i + slot / 2;
          let acc = 0;
          const segs = activeChannels
            .map((ch) => {
              const v = m.by_channel?.[ch]?.revenue || 0;
              const seg = { ch, v, y0: acc, y1: acc + v };
              acc += v;
              return seg;
            })
            .filter((s) => s.v > 0);
          const topIdx = segs.length - 1;
          return (
            <g key={m.month}>
              {segs.map((s, si) => {
                const top = y(s.y1);
                const h = Math.max(1, y(s.y0) - y(s.y1) - 2); // 2px surface gap
                const r = si === topIdx ? 4 : 0;
                return (
                  <path
                    key={s.ch}
                    d={`M${cx - barW / 2},${top + h} L${cx - barW / 2},${top + r} Q${cx - barW / 2},${top} ${cx - barW / 2 + r},${top} L${cx + barW / 2 - r},${top} Q${cx + barW / 2},${top} ${cx + barW / 2},${top + r} L${cx + barW / 2},${top + h} Z`}
                    fill={channelHex(s.ch)}
                    onMouseMove={(e) =>
                      show(e, (
                        <>
                          <div className="tip-title">{fmtMonth(m.month)}</div>
                          <div className="tip-row">
                            <span>{channelLabel(s.ch)}</span>
                            <span className="tip-num">{fmtMoney(s.v)}</span>
                          </div>
                          <div className="tip-row">
                            <span>Total</span>
                            <span className="tip-num">{fmtMoney(m.revenue)}</span>
                          </div>
                        </>
                      ))
                    }
                    onMouseLeave={() => setTip(null)}
                  />
                );
              })}
              <text className="axis-label" x={cx} y={height - 8} textAnchor="middle">
                {fmtMonth(m.month).split(" ")[0]}
              </text>
            </g>
          );
        })}
      </svg>
      <Tip tip={tip} />
      <Legend ids={activeChannels} />
    </div>
  );
}

// Per-channel revenue lines for one client. 2px lines, endpoint direct labels,
// crosshair hover with all series values at the hovered month.
export function ChannelLines({ series, months, height = 220 }) {
  const { tip, setTip, wrapRef, show } = useTip();
  const [hoverI, setHoverI] = useState(null);
  const W = 640;
  const pad = { l: 54, r: 96, t: 14, b: 26 };

  const { lines, yMax } = useMemo(() => {
    const byCh = {};
    for (const r of series) {
      byCh[r.channel] = byCh[r.channel] || {};
      byCh[r.channel][r.month] = r.revenue;
    }
    const ls = CHANNEL_ORDER.filter((ch) => byCh[ch]).map((ch) => ({
      ch,
      values: months.map((m) => byCh[ch][m] || 0),
    }));
    const max = nice(Math.max(...ls.flatMap((l) => l.values)));
    return { lines: ls, yMax: max };
  }, [series, months]);

  const innerW = W - pad.l - pad.r;
  const innerH = height - pad.t - pad.b;
  const x = (i) => pad.l + (innerW * i) / (months.length - 1);
  const y = (v) => pad.t + innerH * (1 - v / yMax);

  const onMove = (e) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - pad.l) / innerW) * (months.length - 1));
    const clamped = Math.max(0, Math.min(months.length - 1, i));
    setHoverI(clamped);
    show(e, (
      <>
        <div className="tip-title">{fmtMonth(months[clamped])}</div>
        {lines.map((l) => (
          <div className="tip-row" key={l.ch}>
            <span>{channelLabel(l.ch)}</span>
            <span className="tip-num">{fmtMoney(l.values[clamped])}</span>
          </div>
        ))}
      </>
    ));
  };

  return (
    <div className="chart-wrap" ref={wrapRef}>
      <svg
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        role="img"
        aria-label="Attributed revenue by channel over time"
        onMouseMove={onMove}
        onMouseLeave={() => {
          setTip(null);
          setHoverI(null);
        }}
      >
        {[0.5, 1].map((f) => (
          <g key={f}>
            <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(yMax * f)} y2={y(yMax * f)} />
            <text className="axis-label" x={pad.l - 8} y={y(yMax * f) + 4} textAnchor="end">
              {fmtMoney(yMax * f)}
            </text>
          </g>
        ))}
        <line className="grid-line" x1={pad.l} x2={W - pad.r} y1={y(0)} y2={y(0)} stroke="var(--border-strong)" />
        {months.map((m, i) => (
          <text key={m} className="axis-label" x={x(i)} y={height - 8} textAnchor="middle">
            {fmtMonth(m).split(" ")[0]}
          </text>
        ))}

        {hoverI != null && (
          <line x1={x(hoverI)} x2={x(hoverI)} y1={pad.t} y2={y(0)} stroke="var(--border-strong)" strokeDasharray="3 3" />
        )}

        {lines.map((l) => {
          const d = l.values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
          const last = l.values[l.values.length - 1];
          return (
            <g key={l.ch}>
              <path d={d} fill="none" stroke={channelHex(l.ch)} strokeWidth="2" strokeLinejoin="round" />
              {hoverI != null && (
                <circle
                  cx={x(hoverI)}
                  cy={y(l.values[hoverI])}
                  r="4"
                  fill={channelHex(l.ch)}
                  stroke="var(--panel)"
                  strokeWidth="2"
                />
              )}
              <text
                x={x(months.length - 1) + 8}
                y={y(last) + 4}
                fontSize="11"
                fontWeight="600"
                fill={channelHex(l.ch)}
              >
                {channelLabel(l.ch)}
              </text>
            </g>
          );
        })}
      </svg>
      <Tip tip={tip} />
    </div>
  );
}
