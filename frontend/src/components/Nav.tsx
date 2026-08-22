import type { MonthSummary } from "../api/client";
import { hasCapability, type Capability, type SessionInfo } from "../capabilities";
import { navigate, type Route } from "../router";

export interface NavProps {
  route: Route;
  months: MonthSummary[];
  capabilities: ReadonlySet<Capability>;
  role: SessionInfo["role"] | null;
}

const links: ReadonlyArray<{
  name: string;
  label: string;
  icon: string;
  capability: Capability;
}> = [
  { name: "overview", label: "Hub", icon: "▦", capability: "schedule.read" },
  { name: "program", label: "Program", icon: "✣", capability: "schedule.read" },
  { name: "exceptions", label: "Excepții", icon: "!", capability: "schedule.read" },
  { name: "jobs", label: "Joburi", icon: "↻", capability: "jobs.read" },
  { name: "close", label: "Management", icon: "▣", capability: "month.close.read" },
];

export function Nav({ route, months, capabilities, role }: NavProps) {
  const activeName = route.name === "store" || route.name === "agent" ? "overview" : route.name;
  const visibleLinks = links.filter((link) => hasCapability(capabilities, link.capability));

  return (
    <div className="sidebar-inner">
      <button className="brand-block" type="button" onClick={() => navigate("overview")}>
        <span className="brand-mark">U</span>
        <span>
          <strong>UniHub Grile</strong>
          <small>{role ? `${role} Console` : "Access Console"}</small>
        </span>
      </button>

      <nav className="app-nav" aria-label="Navigare principală">
        {visibleLinks.map((link) => (
          <button
            key={link.name}
            type="button"
            className={`nav-link ${activeName === link.name ? "active" : ""}`}
            aria-current={activeName === link.name ? "page" : undefined}
            onClick={() => navigate(link.name)}
          >
            <span className="nav-icon" aria-hidden="true">{link.icon}</span>
            <span>{link.label}</span>
          </button>
        ))}
        {role && visibleLinks.length === 0 && (
          <p className="muted">Nu există module operaționale disponibile pentru acest rol.</p>
        )}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-meta-row"><span>Luni disponibile</span><strong>{months.length}</strong></div>
        <div className="sidebar-version">☼ · ◔ · ◌ · ☾</div>
      </div>
    </div>
  );
}
