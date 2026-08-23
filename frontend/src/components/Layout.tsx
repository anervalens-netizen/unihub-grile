import type { ReactNode } from "react";
import { useTablistKeyboardNavigation } from "../accessibility/useTablistKeyboardNavigation";
import "../styles/retail-overrides.css";
import "../styles/responsive.css";
import "../styles/accessibility.css";

export interface LayoutProps {
  children: ReactNode;
  header: ReactNode;
  sidebar: ReactNode;
}

export function Layout({ children, header, sidebar }: LayoutProps) {
  const shellRef = useTablistKeyboardNavigation();

  return (
    <div ref={shellRef} className="app-shell">
      <aside className="app-sidebar">{sidebar}</aside>
      <div className="workspace-shell">
        <header className="workspace-topbar">{header}</header>
        <main className="workspace-main">{children}</main>
      </div>
    </div>
  );
}
