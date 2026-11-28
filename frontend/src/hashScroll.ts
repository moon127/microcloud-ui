import { useEffect, useRef } from "react";
import { useLocation } from "react-router";

/** An alert's link to a section (/ceph#monitors, /settings#notifications): scroll there
 * once ``ready`` (the section is drawn), once per visit (each click on a link is a new
 * location key), not on every refresh. */
export function useHashScroll(ready: unknown): void {
  const { hash, key } = useLocation();
  const scrolled = useRef("");
  useEffect(() => {
    if (!hash || scrolled.current === key || !ready) return;
    const el = document.getElementById(hash.slice(1));
    if (el) {
      el.scrollIntoView?.();
      scrolled.current = key;
    }
  }, [hash, key, ready]);
}
