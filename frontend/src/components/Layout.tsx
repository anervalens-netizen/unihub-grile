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
        <span className="muted">
          Stage S4 · Manager UI · {new Date().getFullYear()}
        </span>
      </footer>
    </div>
  );
}
