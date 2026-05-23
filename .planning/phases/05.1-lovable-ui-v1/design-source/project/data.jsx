// data.jsx — all mock data for the prototype

const COLORS_AVATAR = [
  ["#3390ec", "#6cb8ff"], ["#8774e1", "#c4b5fd"], ["#4dcd5e", "#94e8a0"],
  ["#f5a623", "#fcd57f"], ["#e13b30", "#f59289"], ["#5eaef4", "#a4d2fa"],
  ["#ff7b72", "#ffb1ab"], ["#34a4a4", "#7cd3d3"], ["#b069dc", "#dab1f3"],
  ["#3b82f6", "#93c5fd"], ["#16a34a", "#86efac"], ["#ea580c", "#fdba74"],
];

function avatarStyle(seed) {
  const i = (typeof seed === "string"
    ? seed.split("").reduce((a, c) => a + c.charCodeAt(0), 0)
    : seed) % COLORS_AVATAR.length;
  const [a, b] = COLORS_AVATAR[i];
  return { background: `linear-gradient(135deg, ${a}, ${b})` };
}

function initials(name) {
  return name.split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();
}

// ============================================================
// Workspace
// ============================================================
const WORKSPACE = {
  name: "Acme Growth",
  plan: "Business · Pro",
  user: "Andrew Asachuk",
  email: "andrew@acme.co",
};

// ============================================================
// Campaigns
// ============================================================
const CAMPAIGNS = [
  {
    id: "c1", name: "SaaS Founders Q2 outreach", status: "running",
    agent: "Maya — Discovery SDR", folder: "SaaS founders · US",
    senders: 4, contacts: 1248, sent: 892, replied: 217, leads: 38, handoffs: 12,
    sparkline: [4,6,8,7,12,14,16,15,18,22,24,21,26,28,32,34,38],
    progress: 0.71, startedAt: "Apr 14",
    hours: "09:00 – 19:00 GMT-5", responseRate: 24.3,
  },
  {
    id: "c2", name: "Crypto whales — newsletter pitch", status: "running",
    agent: "Theo — Friendly opener", folder: "Crypto whales · top 500",
    senders: 3, contacts: 487, sent: 412, replied: 98, leads: 21, handoffs: 5,
    sparkline: [2,3,3,5,7,9,8,10,12,14,15,17,19,21,21],
    progress: 0.84, startedAt: "Apr 19",
    hours: "10:00 – 20:00 CET", responseRate: 23.8,
  },
  {
    id: "c3", name: "Re-engage Q1 trial users", status: "paused",
    agent: "Maya — Discovery SDR", folder: "Trial drop-offs",
    senders: 2, contacts: 312, sent: 184, replied: 31, leads: 7, handoffs: 2,
    sparkline: [1,2,3,4,6,5,7,7,7,7],
    progress: 0.59, startedAt: "Apr 02",
    hours: "11:00 – 18:00 MSK", responseRate: 16.8,
  },
  {
    id: "c4", name: "Crypto YouTubers — sponsorship", status: "running",
    agent: "Cleo — Partnerships", folder: "Crypto YT creators",
    senders: 2, contacts: 156, sent: 142, replied: 49, leads: 14, handoffs: 6,
    sparkline: [1,2,4,6,8,9,11,12,12,13,14,14],
    progress: 0.91, startedAt: "Apr 09",
    hours: "13:00 – 22:00 GMT", responseRate: 34.5,
  },
  {
    id: "c5", name: "Web3 dev hiring funnel", status: "draft",
    agent: "Vito — Recruiter", folder: "Solidity devs",
    senders: 0, contacts: 0, sent: 0, replied: 0, leads: 0, handoffs: 0,
    sparkline: [],
    progress: 0, startedAt: "—",
    hours: "—", responseRate: 0,
  },
  {
    id: "c6", name: "Webinar attendees · May 18", status: "scheduled",
    agent: "Theo — Friendly opener", folder: "Webinar RSVPs · May 18",
    senders: 3, contacts: 904, sent: 0, replied: 0, leads: 0, handoffs: 0,
    sparkline: [],
    progress: 0, startedAt: "Starts May 19",
    hours: "09:00 – 17:00 PST", responseRate: 0,
  },
  {
    id: "c7", name: "Series B SaaS — design partners", status: "finished",
    agent: "Cleo — Partnerships", folder: "Series B SaaS · 50-200",
    senders: 4, contacts: 480, sent: 478, replied: 132, leads: 41, handoffs: 18,
    sparkline: [4,8,12,15,20,24,28,32,36,38,40,41,41,41],
    progress: 1, startedAt: "Mar 02 → Apr 11",
    hours: "—", responseRate: 27.6,
  },
];

// ============================================================
// Agents
// ============================================================
const AGENTS = [
  {
    id: "a1", name: "Maya", role: "Discovery SDR",
    desc: "Books intros with SaaS founders. Warm, curious, never pushy.",
    who: "Friendly, curious SDR for AI SaaS startups. Always ends with a single, easy ask.",
    voiceBaseline: "Friendly",
    tone: "Friendly · concise", lang: "EN",
    campaigns: 3, conversations: 1076, leads: 45,
    accent: "#3390ec",
    avatar: "M",
    updated: "2 days ago",
  },
  {
    id: "a2", name: "Theo", role: "Friendly opener",
    desc: "Opens cold dialogs with a soft, on-brand intro. Handles small talk.",
    who: "Warm opener who reads the room. Small talk first, value second.",
    voiceBaseline: "Friendly",
    tone: "Warm · informal", lang: "EN/RU",
    campaigns: 2, conversations: 599, leads: 21,
    accent: "#8774e1",
    avatar: "T",
    updated: "Yesterday",
  },
  {
    id: "a3", name: "Cleo", role: "Partnerships",
    desc: "Pitches sponsorship & integration deals. Knows our deck cold.",
    who: "Professional partnerships rep. Confident with numbers, deck cold-memorized.",
    voiceBaseline: "Professional",
    tone: "Professional · upbeat", lang: "EN",
    campaigns: 2, conversations: 620, leads: 55,
    accent: "#4dcd5e",
    avatar: "C",
    updated: "4 days ago",
  },
  {
    id: "a4", name: "Vito", role: "Recruiter",
    desc: "Reaches out to Solidity devs with role context. Filters availability.",
    who: "Direct, respectful recruiter. Talks tech-to-tech, filters fast.",
    voiceBaseline: "Professional",
    tone: "Direct · respectful", lang: "EN",
    campaigns: 1, conversations: 0, leads: 0,
    accent: "#f5a623",
    avatar: "V",
    updated: "1 week ago",
  },
  {
    id: "a5", name: "Lyra", role: "Onboarding nudger",
    desc: "Re-engages trial users who haven't logged in for 7+ days.",
    who: "Patient nudger for trial drop-offs. Helpful, never guilt-trips.",
    voiceBaseline: "Playful",
    tone: "Helpful · patient", lang: "EN",
    campaigns: 0, conversations: 0, leads: 0,
    accent: "#e13b30",
    avatar: "L",
    updated: "3 weeks ago",
  },
];

// ============================================================
// Contact folders
// ============================================================
const FOLDERS = [
  { id: "f1", name: "SaaS founders · US", contacts: 1248, source: "LinkedIn export · Mar 28", inTg: 0.78, color: "#3390ec" },
  { id: "f2", name: "Crypto whales · top 500", contacts: 487, source: "Manual CSV · Apr 12", inTg: 0.91, color: "#8774e1" },
  { id: "f3", name: "Series B SaaS · 50-200", contacts: 480, source: "Crunchbase scrape", inTg: 0.74, color: "#4dcd5e" },
  { id: "f4", name: "Crypto YT creators", contacts: 156, source: "Manual research", inTg: 0.96, color: "#f5a623" },
  { id: "f5", name: "Trial drop-offs", contacts: 312, source: "Stripe webhook · auto", inTg: 0.82, color: "#5eaef4" },
  { id: "f6", name: "Webinar RSVPs · May 18", contacts: 904, source: "Hopin export", inTg: 0.69, color: "#b069dc" },
  { id: "f7", name: "Solidity devs", contacts: 213, source: "GitHub scrape", inTg: 0.61, color: "#34a4a4" },
];

const CONTACTS_SAMPLE = [
  { fullName: "Sophie Turner", username: "@sophie.t", phone: "+1 415 ··· 2810", source: "linkedin", custom: { company: "UpperCode", role: "CEO" }, inTg: true },
  { fullName: "Chloe Anderson", username: "@chloea", phone: "+1 312 ··· 4441", source: "linkedin", custom: { company: "FinServe", role: "CFO" }, inTg: true },
  { fullName: "Isabella Hart", username: "@bella_h", phone: "+1 646 ··· 8821", source: "crunchbase", custom: { company: "TechWave", role: "CTO" }, inTg: true },
  { fullName: "Samuel Thompson", username: "@samthompson", phone: "+1 408 ··· 1212", source: "linkedin", custom: { company: "BrandLift", role: "CMO" }, inTg: true },
  { fullName: "Michael Anderson", username: null, phone: "+1 503 ··· 9911", source: "manual", custom: { company: "OpsMaster", role: "COO" }, inTg: false },
  { fullName: "David Johnson", username: "@djohnson", phone: "+1 213 ··· 7733", source: "linkedin", custom: { company: "DataSphere", role: "CIO" }, inTg: true },
  { fullName: "Madeline Brooks", username: "@maddyb", phone: "+1 718 ··· 6688", source: "crunchbase", custom: { company: "SecureNet", role: "CSO" }, inTg: true },
  { fullName: "Christopher Brown", username: "@chrisb", phone: "+1 305 ··· 1182", source: "linkedin", custom: { company: "PeopleFirst", role: "CHRO" }, inTg: true },
  { fullName: "Daniel Martinez", username: "@danmtz", phone: "+1 619 ··· 4451", source: "linkedin", custom: { company: "DigitalNest", role: "CDO" }, inTg: true },
  { fullName: "Victoria Lane", username: "@vlane", phone: "+1 802 ··· 9001", source: "manual", custom: { company: "LegalWise", role: "CLO" }, inTg: true },
  { fullName: "Matthew Garcia", username: null, phone: "+1 415 ··· 5520", source: "linkedin", custom: { company: "VisionaryCorp", role: "CVO" }, inTg: false },
  { fullName: "James Wilson", username: "@jwilson", phone: "+1 312 ··· 2244", source: "crunchbase", custom: { company: "ProductForge", role: "CPO" }, inTg: true },
];

// ============================================================
// TG accounts (senders)
// ============================================================
const ACCOUNTS = [
  {
    id: "ac1", name: "Anna Petrova", username: "@anna_p", phone: "+7 901 ··· 8821",
    status: "active", health: 96, campaign: "SaaS Founders Q2 outreach",
    sentToday: 18, limitDaily: 20, sentWeek: 142, limitWeek: 150,
    proxy: "🇩🇪 DE residential", warmupDay: 28, ageDays: 142,
    sparkline: [12,14,15,17,18,18,18,19,18,17,18,18,19,20],
  },
  {
    id: "ac2", name: "Marco Russo", username: "@marco_r", phone: "+39 320 ··· 4412",
    status: "active", health: 92, campaign: "SaaS Founders Q2 outreach",
    sentToday: 14, limitDaily: 20, sentWeek: 118, limitWeek: 150,
    proxy: "🇺🇸 US datacenter", warmupDay: 31, ageDays: 188,
    sparkline: [10,12,13,14,16,17,16,18,17,18,16,17,15,14],
  },
  {
    id: "ac3", name: "Lina Vogel", username: "@lina.v", phone: "+49 176 ··· 9911",
    status: "warmup", health: 71, campaign: "—",
    sentToday: 4, limitDaily: 4, sentWeek: 22, limitWeek: 28,
    proxy: "🇩🇪 DE residential", warmupDay: 9, ageDays: 41,
    sparkline: [2,3,3,3,4,4,4,4,4],
  },
  {
    id: "ac4", name: "Sasha Volkov", username: "@sasha.v", phone: "+7 925 ··· 1102",
    status: "active", health: 88, campaign: "Crypto whales — newsletter pitch",
    sentToday: 19, limitDaily: 20, sentWeek: 147, limitWeek: 150,
    proxy: "🇳🇱 NL residential", warmupDay: 35, ageDays: 220,
    sparkline: [14,16,17,18,18,19,19,18,19,20,19,19,20,19],
  },
  {
    id: "ac5", name: "Yusuf Khan", username: "@yusuf.k", phone: "+44 7700 ··· 3344",
    status: "paused", health: 64, campaign: "Re-engage Q1 trial users",
    sentToday: 0, limitDaily: 20, sentWeek: 67, limitWeek: 150,
    proxy: "🇬🇧 UK residential", warmupDay: 22, ageDays: 95,
    sparkline: [12,14,13,15,8,2,0,0,0],
  },
  {
    id: "ac6", name: "Elena Costa", username: "@elenac", phone: "+34 600 ··· 7788",
    status: "active", health: 94, campaign: "Crypto whales — newsletter pitch",
    sentToday: 16, limitDaily: 20, sentWeek: 132, limitWeek: 150,
    proxy: "🇪🇸 ES residential", warmupDay: 30, ageDays: 161,
    sparkline: [12,14,15,16,17,17,18,17,17,17,16,17,17,16],
  },
  {
    id: "ac7", name: "Hiro Tanaka", username: "@hirot", phone: "+81 90 ··· 2210",
    status: "error", health: 12, campaign: "—",
    sentToday: 0, limitDaily: 20, sentWeek: 41, limitWeek: 150,
    proxy: "🇯🇵 JP residential", warmupDay: 41, ageDays: 270,
    sparkline: [16,18,17,15,9,4,0,0,0],
    error: "Session revoked — re-auth required",
  },
  {
    id: "ac8", name: "Priya Nair", username: "@priya.n", phone: "+91 98 ··· 4421",
    status: "active", health: 89, campaign: "Crypto YouTubers — sponsorship",
    sentToday: 12, limitDaily: 20, sentWeek: 96, limitWeek: 150,
    proxy: "🇮🇳 IN residential", warmupDay: 26, ageDays: 134,
    sparkline: [10,11,12,13,13,14,13,14,13,13,12,12,13,12],
  },
];

// ============================================================
// Conversations (Inbox)
// ============================================================
const CONVOS = [
  {
    id: "v1", contact: "Sophie Turner", username: "@sophie.t",
    company: "UpperCode · CEO", country: "🇺🇸 USA",
    agent: "Maya", campaign: "SaaS Founders Q2 outreach",
    sender: "Anna Petrova",
    status: "lead", unread: 2, lastAt: "2m",
    snippet: "Sounds great — Tuesday 3pm PT works for me. Should I send a calendar invite?",
    starred: true,
    messages: [
      { from: "agent", time: "Apr 28, 14:02", text: "Hi Sophie — Anna here from Acme. Saw UpperCode shipped that programmatic SEO feature last week, that's a sharp move. Quick question: how are you handling outbound on your side right now?" },
      { from: "contact", time: "Apr 28, 15:18", text: "Hey Anna. Mostly through HubSpot sequences + a contractor SDR. Why's that?" },
      { from: "agent", time: "Apr 28, 15:21", text: "Got it. We're building an AI SDR layer that drops into Telegram — most of your ICP lives there if they're outside the US, and reply rates ~3x email. Would a 20-min look make sense? No deck, just a live demo." },
      { from: "contact", time: "Apr 28, 16:44", text: "Maybe. We tried something similar with a vendor in Q1 and it spammed accounts. How do you avoid that?" },
      { from: "agent", time: "Apr 28, 16:47", text: "Fair concern. Three things: per-account rate ceilings (4 / 20 / 150 day-week-month), residential proxies bound to each session, and a warm-up curve that takes new accounts ~30 days to reach full speed. Happy to walk through the dashboard." },
      { from: "contact", time: "Apr 28, 18:09", text: "Ok. Send me times for next week." },
      { from: "agent", time: "Apr 28, 18:10", text: "Tue Apr 30 — 11am or 3pm PT. Wed May 1 — 9am or 4pm PT. Any of those?" },
      { from: "contact", time: "Apr 29, 09:31", text: "Sounds great — Tuesday 3pm PT works for me. Should I send a calendar invite?" },
    ],
    signals: [{ type: "lead", at: "Apr 29, 09:31", note: "Meeting accepted" }],
  },
  {
    id: "v2", contact: "Liam Krieger", username: "@liam.k",
    company: "Riverstack · CTO", country: "🇨🇦 Canada",
    agent: "Maya", campaign: "SaaS Founders Q2 outreach",
    sender: "Marco Russo",
    status: "active", unread: 1, lastAt: "9m",
    snippet: "I'm thrilled to dive into our possibilities. Tell me more about pricing.",
    starred: false,
  },
  {
    id: "v3", contact: "Olivia Reyes", username: "@oliviar",
    company: "Slatewave · Head of Growth", country: "🇲🇽 Mexico",
    agent: "Theo", campaign: "Crypto whales — newsletter pitch",
    sender: "Sasha Volkov",
    status: "active", unread: 0, lastAt: "2h",
    snippet: "Hope you're doing well! Just checking in to see how the trial is going.",
    starred: false,
  },
  {
    id: "v4", contact: "Noah Jansen", username: "@noahj",
    company: "Bitline · Founder", country: "🇳🇱 NL",
    agent: "Cleo", campaign: "Crypto YouTubers — sponsorship",
    sender: "Priya Nair",
    status: "handoff", unread: 0, lastAt: "4h",
    snippet: "I'm excited about what we can invent together — let me loop in our COO.",
    starred: true,
  },
  {
    id: "v5", contact: "Sam Whitaker", username: "@samw",
    company: "Octave Labs · CEO", country: "🇬🇧 UK",
    agent: "Maya", campaign: "SaaS Founders Q2 outreach",
    sender: "Anna Petrova",
    status: "active", unread: 0, lastAt: "5h",
    snippet: "Let me know if you have time to go over them together.",
    starred: false,
  },
  {
    id: "v6", contact: "Ava Morales", username: "@avam",
    company: "Pixelmint · Designer", country: "🇪🇸 ES",
    agent: "Theo", campaign: "Crypto whales — newsletter pitch",
    sender: "Elena Costa",
    status: "active", unread: 0, lastAt: "1d",
    snippet: "The latest version looks great! I have a few minor tweaks I'd like…",
    starred: false,
  },
  {
    id: "v7", contact: "Ethan Shaw", username: "@ethans",
    company: "Lumen · CEO", country: "🇺🇸 USA",
    agent: "Cleo", campaign: "Crypto YouTubers — sponsorship",
    sender: "Priya Nair",
    status: "active", unread: 2, lastAt: "1d",
    snippet: "I'm looking forward to what we can invent. What's the next step?",
    starred: false,
  },
  {
    id: "v8", contact: "Sara Okafor", username: "@sarao",
    company: "Brightwall · COO", country: "🇳🇬 NG",
    agent: "Maya", campaign: "SaaS Founders Q2 outreach",
    sender: "Marco Russo",
    status: "finished", unread: 0, lastAt: "2d",
    snippet: "One thing I wanted to clarify — could you walk me through pricing again?",
    starred: false,
  },
  {
    id: "v9", contact: "Emma Lindqvist", username: "@emmal",
    company: "Northwave · GM", country: "🇸🇪 SE",
    agent: "Theo", campaign: "Crypto whales — newsletter pitch",
    sender: "Sasha Volkov",
    status: "active", unread: 1, lastAt: "3d",
    snippet: "One thing I wanted to ask — what do other agencies usually charge?",
    starred: false,
  },
  {
    id: "v10", contact: "Bryan Cole", username: "@bryanc",
    company: "Ironworks · VP Eng", country: "🇺🇸 USA",
    agent: "Maya", campaign: "SaaS Founders Q2 outreach",
    sender: "Anna Petrova",
    status: "no-reply", unread: 0, lastAt: "4d",
    snippet: "It looks good enough for me — but I need to check with the team.",
    starred: false,
  },
  {
    id: "v11", contact: "Maya Iwata", username: "@mayai",
    company: "Drifthouse · Co-founder", country: "🇯🇵 JP",
    agent: "Cleo", campaign: "Crypto YouTubers — sponsorship",
    sender: "Priya Nair",
    status: "lead", unread: 0, lastAt: "5d",
    snippet: "Loved the deck. Sending it to our CFO — back to you Friday.",
    starred: true,
  },
];

// ============================================================
// LLM call log for the open dialog
// ============================================================
const LLM_TRACE = [
  {
    id: "t1", at: "Apr 29, 09:32", model: "claude-sonnet-4", latency: "1.4s",
    in_tokens: 1842, out_tokens: 86, cost: "$0.012",
    intent: "Confirm meeting + offer calendar invite",
    signals: ["lead_detected: true"],
    tools: [
      { name: "schedule.find_slots", args: { tz: "PT", days: 1 }, result: "Tue Apr 30 15:00 PT — open" },
      { name: "crm.tag_contact", args: { contact_id: "sophie_t", tag: "meeting_booked" }, result: "ok" },
    ],
    system_summary: "Maya — Discovery SDR. Tone: friendly · concise. Goal: book a 20-min intro. Variables: {{first_name}}=Sophie, {{company}}=UpperCode.",
    response: "Perfect — sending a Tuesday Apr 30 · 3:00 PM PT invite right now. I'll add a 20-min agenda + Google Meet link. Looking forward to it.",
    notes: "Marked lead. Handoff suggested to Andrew (CSM).",
  },
  {
    id: "t2", at: "Apr 28, 18:10", model: "claude-sonnet-4", latency: "0.9s",
    in_tokens: 1701, out_tokens: 64, cost: "$0.010",
    intent: "Propose specific meeting slots",
    signals: [],
    tools: [{ name: "schedule.find_slots", args: { tz: "PT", days: 3 }, result: "4 slots returned" }],
    system_summary: "Maya — Discovery SDR. Goal: convert interest into a booked meeting.",
    response: "Tue Apr 30 — 11am or 3pm PT. Wed May 1 — 9am or 4pm PT. Any of those?",
    notes: "Reply expected within 24h. If silent, follow up Wed AM.",
  },
  {
    id: "t3", at: "Apr 28, 16:47", model: "claude-sonnet-4", latency: "2.1s",
    in_tokens: 1564, out_tokens: 142, cost: "$0.014",
    intent: "Address objection about spamming concerns",
    signals: ["objection_handled: rate_limits"],
    tools: [
      { name: "kb.search", args: { q: "rate limits warm-up safety" }, result: "3 chunks" },
    ],
    system_summary: "Maya — Discovery SDR. Knowledge: per-account 4/20/150 corridor, residential proxies, warm-up curve. Recovery: acknowledge concern, address with specifics.",
    response: "Fair concern. Three things: per-account rate ceilings (4 / 20 / 150 day-week-month), residential proxies bound to each session, and a warm-up curve that takes new accounts ~30 days to reach full speed. Happy to walk through the dashboard.",
    notes: "Used kb chunks 2/3 (rate-limit FAQ).",
  },
];

// ============================================================
// Helper: status presentation
// ============================================================
const STATUS_STYLES = {
  running:   { label: "Running",   pill: "pill--green",  dot: "var(--success)" },
  active:    { label: "Active",    pill: "pill--green",  dot: "var(--success)" },
  paused:    { label: "Paused",    pill: "pill--orange", dot: "var(--warning)" },
  draft:     { label: "Draft",     pill: "pill--ghost",  dot: "var(--text-faint)" },
  scheduled: { label: "Scheduled", pill: "pill--blue",   dot: "var(--tg-blue)" },
  finished:  { label: "Finished",  pill: "pill--ghost",  dot: "var(--text-faint)" },
  warmup:    { label: "Warm-up",   pill: "pill--orange", dot: "var(--warning)" },
  error:     { label: "Error",     pill: "pill--red",    dot: "var(--danger)" },
  lead:      { label: "Lead",      pill: "pill--green",  dot: "var(--success)" },
  handoff:   { label: "Handoff",   pill: "pill--purple", dot: "var(--ai-purple)" },
  "no-reply":{ label: "No reply",  pill: "pill--ghost",  dot: "var(--text-faint)" },
};

Object.assign(window, {
  WORKSPACE, CAMPAIGNS, AGENTS, FOLDERS, CONTACTS_SAMPLE, ACCOUNTS, CONVOS, LLM_TRACE,
  STATUS_STYLES, avatarStyle, initials, COLORS_AVATAR,
});
