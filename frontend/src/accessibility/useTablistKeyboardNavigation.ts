import { useEffect, useRef } from "react";

/**
 * Adds the WAI-ARIA horizontal tablist keyboard model to tablists rendered
 * inside the application shell without duplicating navigation logic per page.
 */
export function useTablistKeyboardNavigation() {
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const syncTabStops = () => {
      root.querySelectorAll<HTMLElement>('[role="tablist"]').forEach((tablist) => {
        const tabs = Array.from(tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
        for (const tab of tabs) {
          tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1;
        }
      });
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (!(event.target instanceof HTMLElement)) return;
      const tab = event.target.closest<HTMLButtonElement>('[role="tab"]');
      const tablist = tab?.closest<HTMLElement>('[role="tablist"]');
      if (!tab || !tablist || !root.contains(tablist)) return;

      const tabs = Array.from(tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
      const currentIndex = tabs.indexOf(tab);
      if (currentIndex < 0 || tabs.length === 0) return;

      let nextIndex: number | null = null;
      if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
      if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      if (nextIndex === null) return;

      event.preventDefault();
      const nextTab = tabs[nextIndex];
      nextTab?.focus();
      nextTab?.click();
    };

    syncTabStops();
    const observer = new MutationObserver(syncTabStops);
    observer.observe(root, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["aria-selected"],
    });
    root.addEventListener("keydown", onKeyDown);

    return () => {
      observer.disconnect();
      root.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  return rootRef;
}
