/**
 * useHealth — fetches /health and re-polls every 60 seconds.
 *
 * Used by the System Status panel on the dashboard. Each service is
 * either true / false / undefined, mapped by the panel to green/red/amber.
 */
import { useEffect, useRef, useState } from "react";

import { getHealth } from "../lib/api.js";

const POLL_MS = 60 * 1000;

export function useHealth() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    let id;

    async function tick() {
      try {
        const result = await getHealth();
        if (!cancelledRef.current) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelledRef.current) setError(err);
      }
    }

    tick();
    id = setInterval(tick, POLL_MS);
    return () => {
      cancelledRef.current = true;
      clearInterval(id);
    };
  }, []);

  return { data, error };
}

export default useHealth;
