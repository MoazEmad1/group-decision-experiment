export default function Join({ onJoin, joining, notice }) {
  return (
    <section className="card center">
      <h2>Welcome</h2>
      <p>
        You will discuss with two other participants in two short rounds of about one minute each. After each round,
        everyone votes on who should be the <strong>Decision Maker</strong> for a follow-up task.
      </p>
      {notice && <p className="notice">{notice}</p>}
      <button className="primary" onClick={onJoin} disabled={joining}>
        {joining ? "Joining…" : "Join"}
      </button>
    </section>
  );
}
