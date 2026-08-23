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
    let lastFocused: HTMLElement | null = null;

    const syncTabStops = () => {
      root.querySelectorAll<HTMLElement>('[role="tablist"]').forEach((tablist, tablistIndex) => {
        const tabs = Array.from(tablist.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
        const selectedTab = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") ?? tabs[0];
        for (const [tabIndex, tab] of tabs.entries()) {
          tab.tabIndex = tab === selectedTab ? 0 : -1;
          if (!tab.id) tab.id = `a11y-tab-${tablistIndex}-${tabIndex}`;
          tab.removeAttribute("aria-controls");
        }

        const panel = tablist.nextElementSibling instanceof HTMLElement
          ? tablist.nextElementSibling
          : null;
        if (panel && selectedTab) {
          if (!panel.id) panel.id = `a11y-tabpanel-${tablistIndex}`;
          panel.setAttribute("role", "tabpanel");
          panel.setAttribute("tabindex", "0");
          panel.setAttribute("aria-labelledby", selectedTab.id);
          selectedTab.setAttribute("aria-controls", panel.id);

          if (lastFocused && !lastFocused.isConnected) {
            selectedTab.focus();
            lastFocused = selectedTab;
          }
        }
      });
    };

    const onFocusIn = (event: FocusEvent) => {
      if (event.target instanceof HTMLElement) lastFocused = event.target;
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
    root.addEventListener("focusin", onFocusIn);
    root.addEventListener("keydown", onKeyDown);

    return () => {
      observer.disconnect();
      root.removeEventListener("focusin", onFocusIn);
      root.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  return rootRef;
}
