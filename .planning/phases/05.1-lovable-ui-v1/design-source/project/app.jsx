// app.jsx — main router + tweaks panel integration

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "#3390ec",
  "radius": 14
}/*EDITMODE-END*/;

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // Routing — page may carry { id }
  const [route, setRoute] = React.useState({ page: "dashboard" });

  const onNavigate = page => setRoute({ page });
  const onOpenCampaign = id => setRoute({ page: "campaign", id });
  const onNewCampaign = () => setRoute({ page: "builder" });
  const onOpenConvo = id => setRoute({ page: "inbox", id });

  // Apply tweaks (accent + radius)
  React.useEffect(() => {
    document.documentElement.style.setProperty("--tg-blue", t.accent);
    document.documentElement.style.setProperty("--r-lg", `${t.radius}px`);
  }, [t.accent, t.radius]);

  let body = null;
  if (route.page === "dashboard")  body = <Dashboard onOpenCampaign={onOpenCampaign}/>;
  else if (route.page === "campaigns") body = <Campaigns onOpenCampaign={onOpenCampaign} onNewCampaign={onNewCampaign}/>;
  else if (route.page === "builder")   body = <CampaignBuilder onExit={() => setRoute({ page: "campaigns" })} onLaunched={() => setRoute({ page: "dashboard" })}/>;
  else if (route.page === "campaign")  body = <CampaignDetail campaignId={route.id} onBack={() => setRoute({ page: "campaigns" })} onOpenConvo={onOpenConvo}/>;
  else if (route.page === "inbox")     body = <InboxScreen/>;
  else if (route.page === "agents")    body = <AgentsScreen/>;
  else if (route.page === "contacts")  body = <ContactsScreen/>;
  else if (route.page === "accounts")  body = <AccountsScreen/>;
  else if (route.page === "settings")  body = <StubScreen title="Settings" sub="Workspace, billing, API keys, integrations" icon="settings"/>;
  else if (route.page === "help")      body = <StubScreen title="Help & docs" sub="Guides, API reference, what's new" icon="help"/>;
  else body = <Dashboard onOpenCampaign={onOpenCampaign}/>;

  const navActive = route.page === "campaign" || route.page === "builder" ? "campaigns" : route.page;

  return (
    <div className="app">
      <Sidebar active={navActive} onNavigate={onNavigate}/>
      <main className="app__main">{body}</main>

      {/* Tweaks panel — opt-in via the toolbar toggle */}
      <TweaksPanel title="Tweaks">
        <TweakSection label="Accent color"/>
        <TweakColor
          label="Brand"
          value={t.accent}
          options={["#3390ec", "#8774e1", "#16a34a", "#ea580c", "#0f1419"]}
          onChange={v => setTweak("accent", v)}
        />
        <TweakSection label="Geometry"/>
        <TweakSlider
          label="Card radius"
          value={t.radius} min={4} max={22} step={1} unit="px"
          onChange={v => setTweak("radius", v)}
        />
      </TweaksPanel>
    </div>
  );
}

function StubScreen({ title, sub, icon }) {
  return (
    <>
      <div className="tb">
        <div className="tb__title">{title}</div>
      </div>
      <div className="scroll" style={{ flex: 1, padding: 24, background: "var(--bg-soft)", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div className="card" style={{ padding: 48, textAlign: "center", maxWidth: 480 }}>
          <div style={{ width: 56, height: 56, borderRadius: 16, background: "var(--bg-soft)", color: "var(--text-faint)", display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 14px" }}>
            <Icon name={icon} size={24}/>
          </div>
          <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 6 }}>{title}</div>
          <div className="muted text-sm">{sub}</div>
        </div>
      </div>
    </>
  );
}

const root = ReactDOM.createRoot(document.querySelector("[data-react-root]"));
root.render(<App/>);
