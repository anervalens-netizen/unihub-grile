import type { ReactNode } from "react";
import "../styles/retail-overrides.css";
import "../styles/responsive.css";

export interface LayoutProps {
  children: ReactNode;
  header: ReactNode;
  sidebar: ReactNode;
}

export function Layout({ children, header, sidebar }: LayoutProps) {
  return (
    <div className="app-shell">
      <aside className="app-sidebar">{sidebar}</aside>
      <div className="workspace-shell">
        <header className="workspace-topbar">{header}</header>
        <main className="workspace-main">{children}</main>
      </div>
    </div>
  );
}
