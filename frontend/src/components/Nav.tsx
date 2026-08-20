import type { MonthSummary } from "../api/client";
import { navigate, type Route } from "../router";

export interface NavProps {
  route: Route;
  months: MonthSummary[];
}

export function Nav({ route, months }: NavProps) {
  const links: { name: string; label: string }[] = [
    { name: "overview", label: "Overview" },
    { name: "program", label: "Program" },
    { name: "exceptions", label: "Excepții" },
    { name: "close", label: "Close" },
  ];
  return (
    <nav className="app-nav" aria-label="Navigare principală">
      {links.map((link) => (
        <button
          key={link.name}
          type="button"
          className={`nav-link ${route.name === link.name ? "active" : ""}`}
          aria-current={route.name === link.name ? "page" : undefined}
          onClick={() => navigate(link.name)}
        >
          {link.label}
        </button>
      ))}
      {months.length > 0 && (
        <span className="muted nav-months">
          {months.length} lună{months.length === 1 ? "" : "i"} disponibile
        </span>
      )}
    </nav>
  );
}
