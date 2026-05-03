/**
 * useAuditHistory — fetches /audit/history once, refreshable.
 *
 * Used by the dashboard's audit-queue / recent-audits panels.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { getAuditHistory } from "../lib/api.js";

export function useAuditHistory(pollMs = 0) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const result = await getAuditHistory();
      if (!cancelledRef.current) {
        setData(result);
        setError(null);
      }
    } catch (err) {
      if (!cancelledRef.current) setError(err);
    } finally {
      if (!cancelledRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    refresh();
    let id;
    if (pollMs > 0) id = setInterval(refresh, pollMs);
    return () => {
      cancelledRef.current = true;
      if (id) clearInterval(id);
    };
  }, [refresh, pollMs]);

  return { data, loading, error, refresh };
}

export default useAuditHistory;
