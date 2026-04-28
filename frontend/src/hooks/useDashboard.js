import { startTransition, useEffect, useRef, useState } from "react";

import { fetchDashboard } from "../lib/api.js";

const POLL_INTERVAL_MS = 5000;

export function useDashboard() {
  const [dashboard, setDashboard] = useState(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const activeRef = useRef(false);
  const timeoutRef = useRef(null);
  const requestIdRef = useRef(0);

  async function refreshDashboard({ force = false } = {}) {
    const requestId = ++requestIdRef.current;

    try {
      const payload = await fetchDashboard({ force });
      if (!activeRef.current || requestId !== requestIdRef.current) {
        return payload;
      }

      setError("");
      startTransition(() => {
        setDashboard(payload);
      });
      return payload;
    } catch (fetchError) {
      if (activeRef.current && requestId === requestIdRef.current) {
        setError(fetchError.message);
      }
      throw fetchError;
    } finally {
      if (activeRef.current && requestId === requestIdRef.current) {
        setIsLoading(false);
      }
    }
  }

  useEffect(() => {
    activeRef.current = true;

    async function poll() {
      try {
        await refreshDashboard();
      } catch {
        // Surface the latest error through state, but keep polling.
      }

      if (!activeRef.current) {
        return;
      }

      timeoutRef.current = window.setTimeout(poll, POLL_INTERVAL_MS);
    }

    poll();

    return () => {
      activeRef.current = false;
      if (timeoutRef.current) {
        window.clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  return {
    dashboard,
    error,
    isLoading,
    refreshDashboard,
  };
}
