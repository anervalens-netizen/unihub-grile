/**
 * Tiny hash router (no extra deps).
 *
 * The manager UI uses hash-based navigation so the Vite dev server can
 * serve a single index.html and deep links work without server-side
 * rewrites. Routes are flat: ``#/overview``, ``#/program``, ``#/store/...``,
 * ``#/agent/...``, ``#/exceptions``, ``#/close``. Unknown routes fall back
 * to the overview page.
 */

export interface Route {
  name: string;
  segments: string[];
}

export function currentRoute(): Route {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (!hash) {
    return { name: "overview", segments: [] };
  }
  const all = hash.split("/").filter(Boolean);
  const name = all[0] ?? "overview";
  const segments = all.slice(1);
  return { name, segments };
}

export function navigate(name: string, ...segments: string[]): void {
  const path = segments.length > 0 ? `${name}/${segments.join("/")}` : name;
  window.location.hash = `#/${path}`;
}

export function subscribeRoute(handler: (route: Route) => void): () => void {
  const listener = () => handler(currentRoute());
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
}
