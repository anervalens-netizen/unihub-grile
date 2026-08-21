import type { MonthSummary } from "../api/client";
import { navigate, type Route } from "../router";

export interface NavProps {
  route: Route;
  months: MonthSummary[];
}

const links = [
  { name: "overview", label: "Hub", icon: "▦" },
  { name: "program", label: "Program", icon: "✣" },
  { name: "exceptions", label: "Excepții", icon: "!" },
  { name: "close", label: "Management", icon: "▣" },
];

export function Nav({ route, months }: NavProps) {
  const activeName = route.name === "store" || route.name === "agent" ? "overview" : route.name;

  return (
    <div className="sidebar-inner">
      <button className="brand-block" type="button" onClick={() => navigate("overview")}>
        <span className="brand-mark">U</span>
        <span>
          <strong>UniHub Grile</strong>
          <small>Manager Console</small>
        </span>
      </button>

      <nav className="app-nav" aria-label="Navigare principală">
        {links.map((link) => (
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
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-meta-row"><span>Luni disponibile</span><strong>{months.length}</strong></div>
        <div className="sidebar-version">☼ · ◔ · ◌ · ☾</div>
      </div>
    </div>
  );
}
