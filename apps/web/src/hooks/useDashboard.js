/**
 * useDashboard — fetches /dashboard and re-polls every 5 minutes.
 *
 * Backend caches /dashboard for 5 min in memory, so polling at the same
 * cadence costs ~1 NASA FIRMS + 1 NOAA call per audit-window per host.
 *
 * Returns { data, loading, error, refresh, lastUpdated }.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { getDashboardData } from "../lib/api.js";

const POLL_MS = 5 * 60 * 1000;

export function useDashboard() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const cancelledRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const result = await getDashboardData();
      if (cancelledRef.current) return;
      setData(result);
      setError(null);
      setLastUpdated(new Date());
    } catch (err) {
      if (cancelledRef.current) return;
      setError(err);
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(id);
    };
  }, [refresh]);

  return { data, loading, error, refresh, lastUpdated };
}

export default useDashboard;
