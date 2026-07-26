import { useEffect, useRef } from "react";

const REPORT_INTERVAL_MS = 60_000;
const ACTIVITY_EVENTS = ["pointerdown", "keydown", "touchstart", "scroll", "mousemove"] as const;

/**
 * Reports real browser interaction to nginx at most once per minute.
 * The VM idle timer uses the nginx log timestamp, so cached SPA navigation and
 * mouse/keyboard activity also prevent shutdown without loading the LLM.
 */
export default function ActivityReporter() {
  const lastReportedAt = useRef(0);

  useEffect(() => {
    const reportActivity = () => {
      const now = Date.now();
      if (now - lastReportedAt.current < REPORT_INTERVAL_MS) {
        return;
      }
      lastReportedAt.current = now;

      void fetch("/activity", {
        method: "POST",
        cache: "no-store",
        keepalive: true,
      }).catch(() => {
        // A failed heartbeat must never interrupt the user's work in the UI.
      });
    };

    for (const eventName of ACTIVITY_EVENTS) {
      window.addEventListener(eventName, reportActivity, { passive: true });
    }

    return () => {
      for (const eventName of ACTIVITY_EVENTS) {
        window.removeEventListener(eventName, reportActivity);
      }
    };
  }, []);

  return null;
}
