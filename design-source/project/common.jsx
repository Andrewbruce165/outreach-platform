// common.jsx — shared small components: sparklines, bars, status pills, avatar

function StatusPill({ status, dot = true }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.draft;
  return (
    <span className={`pill ${s.pill}`}>
      {dot && <span className="pill__dot" style={{ background: s.dot }} />}
      {s.label}
    </span>
  );
}

function Avatar({ name, size = "md", src }) {
  const cls = size === "sm" ? "avatar avatar--sm"
            : size === "lg" ? "avatar avatar--lg"
            : size === "xl" ? "avatar avatar--xl"
            : "avatar";
  return (
    <div className={cls} style={avatarStyle(name || "x")}>
      {initials(name || "?")}
    </div>
  );
}

// SVG line sparkline
function Sparkline({ data = [], width = 100, height = 30, color = "var(--tg-blue)", fill = true, strokeWidth = 1.5 }) {
  if (!data || data.length === 0) {
    return <div style={{ width, height, display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)", fontSize: 11 }}>—</div>;
  }
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const range = max - min || 1;
  const step = width / Math.max(data.length - 1, 1);
  const pts = data.map((v, i) => {
    const x = i * step;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return [x, y];
  });
  const linePath = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${width} ${height} L 0 ${height} Z`;
  const gid = "sg-" + Math.random().toString(36).slice(2, 8);
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: "block" }}>
      {fill && (
        <>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.22"/>
              <stop offset="100%" stopColor={color} stopOpacity="0"/>
            </linearGradient>
          </defs>
          <path d={areaPath} fill={`url(#${gid})`}/>
        </>
      )}
      <path d={linePath} stroke={color} strokeWidth={strokeWidth} fill="none" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  );
}

// CSS-based bar chart — scales cleanly without SVG distortion
function BarChart({ data = [], height = 80, color = "var(--tg-blue)", labels }) {
  if (!data.length) return null;
  const max = Math.max(...data, 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 4, height, padding: "0 2px" }}>
        {data.map((v, i) => (
          <div key={i} title={`${labels && labels[i] ? labels[i] + " — " : ""}${v}`}
            style={{
              flex: 1, minWidth: 0,
              height: `${(v / max) * 100}%`,
              background: color, opacity: 0.85,
              borderRadius: 4,
              transition: "height 0.4s",
            }}/>
        ))}
      </div>
      {labels && (
        <div style={{ display: "flex", gap: 4, padding: "0 2px" }}>
          {labels.map((l, i) => (
            <div key={i} style={{ flex: 1, textAlign: "center", fontSize: 10, color: "var(--text-faint)", whiteSpace: "nowrap" }}>
              {l}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Stacked area chart for live messages
function StackedAreaChart({ series = [], width = 600, height = 180, labels, showAxis = true }) {
  // series: [{ name, color, data: number[] }]
  if (!series.length || !series[0].data.length) return null;
  const n = series[0].data.length;
  const stacked = []; // cumulative per index
  for (let i = 0; i < n; i++) {
    let cum = 0;
    const col = [];
    for (let s = 0; s < series.length; s++) {
      cum += series[s].data[i];
      col.push(cum);
    }
    stacked.push(col);
  }
  const max = Math.max(...stacked.map(c => c[c.length - 1]), 1);
  const step = width / Math.max(n - 1, 1);

  const yFor = (v) => height - (v / max) * (height - 12) - 6;

  return (
    <svg width="100%" height={height + 20} viewBox={`0 0 ${width} ${height + 20}`} preserveAspectRatio="none">
      {/* grid */}
      {showAxis && [0.25, 0.5, 0.75].map(p => (
        <line key={p} x1="0" x2={width} y1={height - (height - 12) * p - 6} y2={height - (height - 12) * p - 6} stroke="var(--divider)" strokeWidth="1"/>
      ))}
      {series.map((s, sIdx) => {
        const top = stacked.map((c, i) => [i * step, yFor(c[sIdx])]);
        const bot = sIdx === 0
          ? Array.from({ length: n }, (_, i) => [i * step, height - 6])
          : stacked.map((c, i) => [i * step, yFor(c[sIdx - 1])]);
        const pathTop = top.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
        const pathBot = bot.slice().reverse().map(([x, y]) => `L ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
        const linePath = pathTop;
        const areaPath = `${pathTop} ${pathBot} Z`;
        return (
          <g key={sIdx}>
            <path d={areaPath} fill={s.color} opacity="0.18"/>
            <path d={linePath} fill="none" stroke={s.color} strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"/>
          </g>
        );
      })}
      {labels && labels.map((l, i) => (
        <text key={i} x={i * step} y={height + 14} textAnchor={i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle"} fontSize="10" fill="var(--text-faint)">{l}</text>
      ))}
    </svg>
  );
}

// Donut
function Donut({ value = 0.65, size = 80, stroke = 8, color = "var(--tg-blue)", track = "var(--bg-soft)", label }) {
  const r = (size - stroke) / 2;
  const C = 2 * Math.PI * r;
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size/2} cy={size/2} r={r} stroke={track} strokeWidth={stroke} fill="none"/>
        <circle
          cx={size/2} cy={size/2} r={r}
          stroke={color} strokeWidth={stroke} fill="none"
          strokeLinecap="round"
          strokeDasharray={`${C * value} ${C}`}
        />
      </svg>
      {label !== undefined && (
        <div style={{
          position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 13, fontWeight: 600, fontVariantNumeric: "tabular-nums",
        }}>{label}</div>
      )}
    </div>
  );
}

// Progress bar with green corridor (for rate limits 4/20/150)
function CorridorBar({ value, limit, warn = 0.8 }) {
  const pct = Math.min(value / limit, 1);
  const color = pct >= 1 ? "var(--danger)" : pct >= warn ? "var(--warning)" : "var(--success)";
  return (
    <div style={{ position: "relative", height: 6, background: "var(--bg-soft)", borderRadius: 999, overflow: "hidden" }}>
      <div style={{ position: "absolute", inset: 0, width: `${pct * 100}%`, background: color, borderRadius: 999, transition: "width 0.4s" }}/>
    </div>
  );
}

// Empty state
function Empty({ icon = "info", title, body, action }) {
  return (
    <div style={{
      display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
      padding: 56, gap: 12, textAlign: "center", color: "var(--text-muted)",
    }}>
      <div style={{
        width: 56, height: 56, borderRadius: 16, background: "var(--bg-soft)",
        display: "flex", alignItems: "center", justifyContent: "center", color: "var(--text-faint)",
      }}>
        <Icon name={icon} size={24}/>
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text)" }}>{title}</div>
      {body && <div style={{ maxWidth: 360, fontSize: 13 }}>{body}</div>}
      {action}
    </div>
  );
}

// Section header with title + actions
function SectionHead({ title, sub, right }) {
  return (
    <div style={{
      display: "flex", alignItems: "flex-end", justifyContent: "space-between",
      gap: 12, marginBottom: 14,
    }}>
      <div>
        <div style={{ fontSize: 16, fontWeight: 600, letterSpacing: "-0.01em" }}>{title}</div>
        {sub && <div className="muted text-sm" style={{ marginTop: 2 }}>{sub}</div>}
      </div>
      {right}
    </div>
  );
}

// Tag chip
function Tag({ children, onRemove, color }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      padding: "3px 10px", height: 24,
      background: "var(--bg-soft)", borderRadius: 6,
      fontSize: 11.5, fontWeight: 500,
      color: color || "var(--text-soft)",
      textTransform: "uppercase", letterSpacing: "0.04em",
      border: "1px solid var(--border)",
    }}>
      {children}
      {onRemove && (
        <button onClick={onRemove} style={{ display: "flex", color: "var(--text-faint)" }}>
          <Icon name="x" size={11}/>
        </button>
      )}
    </span>
  );
}

// CountUp -> simple animated number
function CountUp({ to, duration = 800, prefix = "", suffix = "", className }) {
  const [v, setV] = React.useState(0);
  React.useEffect(() => {
    let raf;
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setV(Math.round(to * eased));
      if (t < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, duration]);
  return <span className={className}>{prefix}{v.toLocaleString()}{suffix}</span>;
}

// Live "wire" pulse — flowing dots between source/target points
function LiveWire({ from, to, color = "var(--tg-blue)", duration = 2200, delay = 0 }) {
  // from/to: [x, y] in svg coords (0..1)
  const cx = (from[0] + to[0]) / 2;
  const cy = Math.min(from[1], to[1]) - 0.1;
  const path = `M ${from[0]} ${from[1]} Q ${cx} ${cy} ${to[0]} ${to[1]}`;
  const id = "wire-" + Math.random().toString(36).slice(2, 8);
  return (
    <g>
      <path d={path} stroke={color} strokeOpacity="0.2" strokeWidth="0.004" fill="none"/>
      <circle r="0.012" fill={color}>
        <animateMotion dur={`${duration}ms`} repeatCount="indefinite" begin={`${delay}ms`} path={path}/>
        <animate attributeName="opacity" values="0;1;1;0" dur={`${duration}ms`} repeatCount="indefinite" begin={`${delay}ms`}/>
      </circle>
    </g>
  );
}

Object.assign(window, {
  StatusPill, Avatar, Sparkline, BarChart, StackedAreaChart, Donut, CorridorBar,
  Empty, SectionHead, Tag, CountUp, LiveWire,
});
