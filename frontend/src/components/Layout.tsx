import type { ReactNode } from "react";

export interface LayoutProps {
  children: ReactNode;
  header: ReactNode;
}

export function Layout({ children, header }: LayoutProps) {
  return (
    <div className="app-shell">
      {header}
      <main className="app-main">{children}</main>
      <footer className="app-footer">
        <span className="muted">Stage S1 · Foundation · 2026-08-20</span>
      </footer>
    </div>
  );
}