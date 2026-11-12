// Deliberately no player count: how fast others "arrive" must not hint at who is human or AI.
export default function Waiting() {
  return (
    <section className="card center">
      <div className="spinner" aria-hidden="true" />
      <h2>Waiting for other participants</h2>
      <p className="muted">The discussion starts automatically once everyone has joined.</p>
    </section>
  );
}
