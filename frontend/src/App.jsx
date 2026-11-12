import { useEffect, useState } from "react";

export default function App() {
  const [health, setHealth] = useState("checking...");

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then((d) => setHealth(`backend ${d.status}, db ${d.db}`))
      .catch(() => setHealth("backend unreachable"));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 24 }}>
      <h1>Group Decision Study</h1>
      <p>{health}</p>
    </main>
  );
}
