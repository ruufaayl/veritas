import { useState } from "react";

import { runAudit } from "../lib/api.js";

export function useAudit() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function start(serial) {
    setLoading(true);
    setError(null);
    try {
      const result = await runAudit(serial);
      setData(result);
      return result;
    } catch (err) {
      setError(err);
      throw err;
    } finally {
      setLoading(false);
    }
  }

  return { data, loading, error, start };
}
