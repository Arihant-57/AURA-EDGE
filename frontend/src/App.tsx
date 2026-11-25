import { useEffect, useState } from "react";
import { Ask, AssistantView, useVoice, VoiceButtons } from "./Assistant";
import { Automation, Routines } from "./Automation";
import { Home, Rooms, useHome } from "./Home";
import { Household, householdTabs } from "./Household";
import { startLive, useConnection, useLive, useResource } from "./live";
import { Planner, plannerTabs } from "./Planner";
import { NotificationCenter, Preferences, Settings, settingsTabs, useNotices } from "./Settings";
import { NoticePrefs } from "./api/desktop";
import { Icon, IconButton, Tabs } from "./ui";

const pages = ["Home", "Automation", "Rooms", "Tasks", "Routines", "Assistant", "Household", "Settings"] as const;
type Page = typeof pages[number];
const headings: Record<Page, string> = {
  Home: "Home", Automation: "Automation", Rooms: "Rooms", Tasks: "Tasks & Planner",
  Routines: "Routines", Assistant: "Assistant", Household: "Household", Settings: "Settings",
};
const subtitles: Partial<Record<Page, string>> = {
  Automation: "Schedule device actions and review their results.",
  Rooms: "Explore your simulated home and control each room’s devices.",
  Tasks: "Your tasks, calendar and reminders.",
  Routines: "Group home actions and run them together.",
  Assistant: "Talk or type in English, Hindi or Kannada.",
  Household: "Shopping, pantry, recipes, expenses and saved deliveries.",
};
function defaultTab(page: Page) {
  if (page === "Tasks") return "Tasks";
  if (page === "Household") return "Groceries";
  if (page === "Automation") return "Schedules";
  if (page === "Settings") return "General";
  return "";
}

export function App() {
  const [navigation, setNavigation] = useState<{ page: Page; tab: string }>({ page: "Home", tab: "" });
  const [smallNav, setSmallNav] = useState(false);
  const [showNotices, setShowNotices] = useState(false);
  const [alert, setAlert] = useState("");
  const [prefs, setPrefs] = useState<Preferences>();
  const [bridgeReady, setBridgeReady] = useState(Boolean(window.auraDesktop));
  const online = useConnection();
  const voice = useVoice(online);
  const home = useHome();
  const notices = useNotices();
  const noticePrefs = useResource<NoticePrefs>("/notifications/preferences", /^notification_preferences_/);
  const timezone = noticePrefs.data?.timezone_name ?? "Asia/Kolkata";
  const { page, tab } = navigation;

  function open(destination: string, next?: string) {
    const target = pages.find((item) => item === destination);
    if (!target) return;
    setNavigation({ page: target, tab: next ?? defaultTab(target) });
    setShowNotices(false);
  }

  useLive((event) => {
    if (event.type === "notification_created" && event.payload.visual_enabled && !window.auraDesktop) {
      const notice = event.payload.notification as { title?: string; message?: string } | undefined;
      if (notice) setAlert(notice.title ?? notice.message ?? "");
    }
    if (event.type === "desktop_voice_error") setAlert(String(event.payload.message ?? ""));
  });

  useEffect(() => {
    const initialize = () => setBridgeReady(true);
    window.addEventListener("aura-desktop-ready", initialize);
    if (window.auraDesktop) initialize();
    return () => window.removeEventListener("aura-desktop-ready", initialize);
  }, []);

  useEffect(() => {
    if ((import.meta.env.DEV && import.meta.env.MODE !== "test") || bridgeReady) startLive();
  }, [bridgeReady]);

  useEffect(() => {
    const bridge = window.auraDesktop;
    if (!bridgeReady || !bridge) return;
    const receive = (raw: string, initial = false) => {
      const value = JSON.parse(raw) as Preferences;
      setPrefs(value);
      if (initial && value.page) open(value.page);
    };
    const onPreferences = (raw: string) => receive(raw);
    bridge.preferences((raw) => receive(raw, true));
    bridge.preferencesChanged.connect(onPreferences);
    bridge.navigate.connect(open);
    return () => {
      bridge.preferencesChanged.disconnect?.(onPreferences);
      bridge.navigate.disconnect?.(open);
    };
  }, [bridgeReady]);

  useEffect(() => {
    if (!alert) return;
    const timer = window.setTimeout(() => setAlert(""), 5500);
    return () => window.clearTimeout(timer);
  }, [alert]);

  const bridge = window.auraDesktop;
  const devices = home.devices.data ?? [];
  const unread = notices.data?.unread_count ?? 0;
  const plannerTab = plannerTabs.find((item) => item === tab) ?? "Tasks";
  const householdTab = householdTabs.find((item) => item === tab) ?? "Groceries";
  const settingsTab = settingsTabs.find((item) => item === tab) ?? "General";
  const choose = (next: string) => open(page, next);
  return <div className={`desktop-stage ${window.auraNative || bridgeReady ? "native" : "standalone"} ${prefs?.reducedMotion ? "reduced-motion" : ""}`}>
    <div className={`shell ${prefs?.translucent === false ? "opaque" : ""}`}>
      <header className="topbar">
        <div className="brand" onPointerDown={bridge ? () => bridge.windowAction("move") : undefined}>
          <Icon name="logo" size={35}/><h1>AURA <span>EDGE</span></h1><span className="brand-caption">AI Home Assistant</span>
        </div>
        <Ask voice={voice} header onSend={() => open("Assistant")}/>
        <VoiceButtons voice={voice}/>
        <div className="header-actions">
          <span className={`connection-indicator ${online ? "online" : ""}`} role="status" title={voice.label} aria-label={voice.label}/>
          <div className="bell-wrap"><IconButton icon="bell" label={`Notifications${unread ? `, ${unread} unread` : ""}`} onClick={() => setShowNotices(!showNotices)}/>{unread > 0 && <i/>}</div>
          <IconButton icon="calendar" label="Calendar" onClick={() => open("Tasks", "Calendar")}/>
          {bridge && <>
            <IconButton icon="pin" label={prefs?.pinned ? "Unpin dashboard" : "Pin dashboard"} active={prefs?.pinned} onClick={() => bridge.windowAction("pin")}/>
            <IconButton icon="minimize" label="Minimize to pet" onClick={() => bridge.windowAction("minimize")}/>
            <IconButton icon="shutdown" label="Shutdown AURA" onClick={() => bridge.windowAction("shutdown")}/>
          </>}
        </div>
      </header>
      <div className={`shell-body ${smallNav ? "small-nav" : ""}`}>
        <nav aria-label="Main navigation" className="sidebar">
          {pages.map((item) => <button key={item} type="button" aria-current={page === item ? "page" : undefined} aria-label={item} title={item} onClick={() => open(item)}><Icon name={item.toLowerCase()}/><span>{item}</span></button>)}
          <IconButton icon={smallNav ? "right" : "left"} label={smallNav ? "Expand navigation" : "Collapse navigation"} onClick={() => setSmallNav(!smallNav)}/>
        </nav>
        <main className="workspace" key={page}>
          {!online && <div className="connection-banner" role="status">Local backend disconnected. Reconnecting…</div>}
          {page !== "Home" && <div className="page-heading"><h2>{headings[page]}</h2>{subtitles[page] && <p>{subtitles[page]}</p>}</div>}
          {page === "Home" && <Home data={home} timezone={timezone} open={open} briefingLanguage={voice.state.session_language ?? "auto"}/>}
          {page === "Rooms" && <div className="rooms-grid"><Rooms data={home} schedule={(id) => open("Automation", id)}/></div>}
          {page === "Automation" && <><Tabs options={["Schedules", "Activities"]} selected={tab === "Activities" ? "Activities" : "Schedules"} choose={choose}/><Automation key={tab} devices={devices} timezone={timezone} requested={tab}/></>}
          {page === "Routines" && <Routines devices={devices} schedule={(id) => open("Automation", `routine:${id}`)}/>}
          {page === "Tasks" && <><Tabs options={plannerTabs} selected={plannerTab} choose={choose}/><Planner key={tab} requested={tab} timezone={timezone}/></>}
          {page === "Assistant" && <AssistantView voice={voice}/>}
          {page === "Household" && <><Tabs options={householdTabs} selected={householdTab} choose={choose}/><Household tab={householdTab} timezone={timezone}/></>}
          {page === "Settings" && <div className="settings-grid"><div className="settings-nav"><Tabs options={settingsTabs} selected={settingsTab} choose={choose}/></div><Settings key={settingsTab} tab={settingsTab} voice={voice} prefs={prefs} timezone={timezone}/></div>}
        </main>
      </div>
      {showNotices && <NotificationCenter notices={notices} timezone={timezone} close={() => setShowNotices(false)}/>}
      {alert && <button className="toast" role="status" onClick={() => setAlert("")}>{alert}<Icon name="close"/></button>}
    </div>
  </div>;
}

export default App;
