import { useEffect, useState } from "react";

/**
 * Seed component for the React port of Insyte Studio.
 *
 * The full workspace currently ships as a build-free SPA in `src/insyte/studio_dist/`.
 * This is the starting point for the richer React UI; it verifies the API contract and
 * gives contributors a place to build out the shell, chat and analysis views.
 */
type Status = {
  project: string;
  schema: { tables: number; scanned: boolean };
  analytics_mode: string;
};

export function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setError("API unreachable"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 720 }}>
      <h1>Insyte Studio</h1>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      {status ? (
        <p>
          Project <strong>{status.project}</strong> · {status.schema.tables} tables ·{" "}
          {status.analytics_mode} mode
        </p>
      ) : (
        <p>Loading…</p>
      )}
      <p style={{ color: "#6b7280" }}>
        This React app is the development seed. Build it with <code>npm run build</code> to
        replace the shipped no-build workspace.
      </p>
    </main>
  );
}
