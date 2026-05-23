// icons.jsx — tiny stroke icon set (1.5px stroke, 20px nominal)
// Single source for all icons used across the app.

const Icon = ({ name, size = 18, color = "currentColor", strokeWidth = 1.6, style }) => {
  const paths = ICON_PATHS[name];
  if (!paths) return null;
  return (
    <svg
      width={size} height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={style}
      aria-hidden="true"
    >
      {paths}
    </svg>
  );
};

const ICON_PATHS = {
  // nav
  dashboard: (<>
    <rect x="3" y="3" width="7" height="9" rx="2"/>
    <rect x="14" y="3" width="7" height="5" rx="2"/>
    <rect x="14" y="12" width="7" height="9" rx="2"/>
    <rect x="3" y="16" width="7" height="5" rx="2"/>
  </>),
  campaigns: (<>
    <path d="M3 11l18-7-5 18-4-8-9-3z"/>
  </>),
  inbox: (<>
    <path d="M22 12h-6l-2 3h-4l-2-3H2"/>
    <path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>
  </>),
  agents: (<>
    <path d="M9 11V8a3 3 0 0 1 6 0v3"/>
    <rect x="5" y="11" width="14" height="10" rx="3"/>
    <circle cx="9.5" cy="16" r="1" fill="currentColor" stroke="none"/>
    <circle cx="14.5" cy="16" r="1" fill="currentColor" stroke="none"/>
    <path d="M12 3v2"/>
  </>),
  contacts: (<>
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/>
    <circle cx="9" cy="7" r="4"/>
    <path d="M23 21v-2a4 4 0 0 0-3-3.87"/>
    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
  </>),
  accounts: (<>
    <path d="M21.5 4.5l-19 9 7 2 2 7 10-18z"/>
  </>),
  settings: (<>
    <circle cx="12" cy="12" r="3"/>
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
  </>),
  help: (<>
    <circle cx="12" cy="12" r="10"/>
    <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/>
    <path d="M12 17h.01"/>
  </>),

  // ui
  search: (<>
    <circle cx="11" cy="11" r="7"/>
    <path d="M21 21l-4.35-4.35"/>
  </>),
  bell: (<>
    <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/>
    <path d="M13.73 21a2 2 0 0 1-3.46 0"/>
  </>),
  plus: (<><path d="M12 5v14M5 12h14"/></>),
  minus: (<><path d="M5 12h14"/></>),
  check: (<><path d="M20 6L9 17l-5-5"/></>),
  x: (<><path d="M18 6L6 18M6 6l12 12"/></>),
  chevron_down: (<><path d="M6 9l6 6 6-6"/></>),
  chevron_right: (<><path d="M9 18l6-6-6-6"/></>),
  chevron_left: (<><path d="M15 18l-9-6 9-6"/></>),
  arrow_right: (<><path d="M5 12h14M12 5l7 7-7 7"/></>),
  arrow_up_right: (<><path d="M7 17L17 7M7 7h10v10"/></>),
  arrow_down_right: (<><path d="M7 7l10 10M17 7v10H7"/></>),
  more: (<>
    <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>
    <circle cx="19" cy="12" r="1" fill="currentColor" stroke="none"/>
    <circle cx="5" cy="12" r="1" fill="currentColor" stroke="none"/>
  </>),
  filter: (<><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/></>),
  export: (<>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <path d="M17 8l-5-5-5 5"/>
    <path d="M12 3v12"/>
  </>),
  download: (<>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <path d="M7 10l5 5 5-5"/>
    <path d="M12 15V3"/>
  </>),
  upload: (<>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
    <path d="M17 8l-5-5-5 5"/>
    <path d="M12 3v12"/>
  </>),
  refresh: (<>
    <path d="M23 4v6h-6"/>
    <path d="M1 20v-6h6"/>
    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>
    <path d="M20.49 15A9 9 0 0 1 5.64 18.36L1 14"/>
  </>),
  calendar: (<>
    <rect x="3" y="4" width="18" height="18" rx="2"/>
    <path d="M16 2v4M8 2v4M3 10h18"/>
  </>),
  clock: (<>
    <circle cx="12" cy="12" r="10"/>
    <path d="M12 6v6l4 2"/>
  </>),
  folder: (<>
    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
  </>),
  file_csv: (<>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <path d="M14 2v6h6"/>
  </>),
  play: (<><path d="M5 3l14 9-14 9V3z" fill="currentColor"/></>),
  pause: (<>
    <rect x="6" y="4" width="4" height="16" fill="currentColor" stroke="none" rx="1"/>
    <rect x="14" y="4" width="4" height="16" fill="currentColor" stroke="none" rx="1"/>
  </>),
  bolt: (<><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></>),
  sparkles: (<>
    <path d="M12 3l1.9 4.6L18 9l-4.1 1.4L12 15l-1.9-4.6L6 9l4.1-1.4L12 3z" fill="currentColor" stroke="none"/>
    <path d="M19 14l.95 2.3L22 17l-2.05.7L19 20l-.95-2.3L16 17l2.05-.7L19 14z" fill="currentColor" stroke="none"/>
    <path d="M5 14l.95 2.3L8 17l-2.05.7L5 20l-.95-2.3L2 17l2.05-.7L5 14z" fill="currentColor" stroke="none"/>
  </>),
  send: (<><path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/></>),
  user: (<>
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
    <circle cx="12" cy="7" r="4"/>
  </>),
  message: (<>
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
  </>),
  message_circle: (<>
    <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
  </>),
  shield: (<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></>),
  trending_up: (<>
    <path d="M23 6l-9.5 9.5-5-5L1 18"/>
    <path d="M17 6h6v6"/>
  </>),
  pie: (<>
    <path d="M21.21 15.89A10 10 0 1 1 8 2.83"/>
    <path d="M22 12A10 10 0 0 0 12 2v10z"/>
  </>),
  flag: (<>
    <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>
    <line x1="4" y1="22" x2="4" y2="15"/>
  </>),
  zap: (<><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></>),
  edit: (<>
    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
  </>),
  copy: (<>
    <rect x="9" y="9" width="13" height="13" rx="2"/>
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
  </>),
  trash: (<>
    <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
  </>),
  phone: (<>
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/>
  </>),
  globe: (<>
    <circle cx="12" cy="12" r="10"/>
    <path d="M2 12h20"/>
    <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
  </>),
  qr: (<>
    <rect x="3" y="3" width="7" height="7" rx="1"/>
    <rect x="14" y="3" width="7" height="7" rx="1"/>
    <rect x="3" y="14" width="7" height="7" rx="1"/>
    <path d="M14 14h3v3M21 14v.01M14 17v.01M17 21h.01M21 21v-3M14 21h.01M21 17v.01"/>
  </>),
  cpu: (<>
    <rect x="4" y="4" width="16" height="16" rx="2"/>
    <rect x="9" y="9" width="6" height="6"/>
    <path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>
  </>),
  star: (<><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></>),
  pin: (<>
    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/>
    <circle cx="12" cy="10" r="3"/>
  </>),
  shuffle: (<>
    <path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>
  </>),
  hash: (<>
    <line x1="4" y1="9" x2="20" y2="9"/>
    <line x1="4" y1="15" x2="20" y2="15"/>
    <line x1="10" y1="3" x2="8" y2="21"/>
    <line x1="16" y1="3" x2="14" y2="21"/>
  </>),
  layers: (<>
    <path d="M12 2l10 5-10 5L2 7l10-5z"/>
    <path d="M2 17l10 5 10-5"/>
    <path d="M2 12l10 5 10-5"/>
  </>),
  brain: (<>
    <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2z"/>
    <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z"/>
  </>),
  tool: (<>
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
  </>),
  link: (<>
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
  </>),
  paperclip: (<>
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>
  </>),
  smile: (<>
    <circle cx="12" cy="12" r="10"/>
    <path d="M8 14s1.5 2 4 2 4-2 4-2"/>
    <line x1="9" y1="9" x2="9.01" y2="9"/>
    <line x1="15" y1="9" x2="15.01" y2="9"/>
  </>),
  alert_triangle: (<>
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
    <line x1="12" y1="9" x2="12" y2="13"/>
    <line x1="12" y1="17" x2="12.01" y2="17"/>
  </>),
  info: (<>
    <circle cx="12" cy="12" r="10"/>
    <line x1="12" y1="16" x2="12" y2="12"/>
    <line x1="12" y1="8" x2="12.01" y2="8"/>
  </>),
  eye: (<>
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
    <circle cx="12" cy="12" r="3"/>
  </>),
  flask: (<>
    <path d="M10 2h4v6l5 9a3 3 0 0 1-2.6 4.5H7.6A3 3 0 0 1 5 17l5-9V2z"/>
    <path d="M8.5 13h7"/>
  </>),
  rocket: (<>
    <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/>
    <path d="M12 15l-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/>
    <path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>
  </>),
};

Object.assign(window, { Icon });
