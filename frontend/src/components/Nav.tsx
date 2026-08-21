import type { MonthSummary } from "../api/client";
import { navigate, type Route } from "../router";

export interface NavProps {
  route: Route;
  months: MonthSummary[];
}

const links = [
  { name: "overview", label: "Command Center", icon: "⌂" },
  { name: "program", label: "Calendar", icon: "▦" },
  { name: "exceptions", label: "Excepții", icon: "!" },
  { name: "close", label: "Închidere lună", icon: "✓" },
];

export function Nav({ route, months }: NavProps) {
  return (
    <div className="sidebar-inner">
      <button className="brand-block" type="button" onClick={() => navigate("overview")}>
        <span className="brand-mark">U</span>
        <span>
          <strong>UniHub</strong>
          <small>Grile Command</small>
        </span>
      </button>

      <nav className="app-nav" aria-label="Navigare principală">
        <span className="nav-section-label">OPERAȚIONAL</span>
        {links.map((link) => (
          <button
            key={link.name}
            type="button"
            className={`nav-link ${route.name === link.name ? "active" : ""}`}
            aria-current={route.name === link.name ? "page" : undefined}
            onClick={() => navigate(link.name)}
          >
            <span className="nav-icon" aria-hidden="true">{link.icon}</span>
            <span>{link.label}</span>
          </button>
        ))}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-meta-row">
          <span>Luni disponibile</span>
          <strong>{months.length}</strong>
        </div>
        <div className="sidebar-version">UniHub Grile · Manager Console</div>
      </div>
    </div>
  );
}
