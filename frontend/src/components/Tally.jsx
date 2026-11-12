import { playerName } from "../labels.js";

// Vote counts only; who voted for whom is never sent by the server.
export default function Tally({ title, tally, you }) {
  if (!tally) return null;
  const total = Object.keys(tally.counts).length || 1;

  return (
    <div className="tally">
      <h3>{title}</h3>
      <ul>
        {Object.entries(tally.counts).map(([label, count]) => (
          <li key={label}>
            <span className="tally-name">{playerName(label, you)}</span>
            <span className="tally-bar">
              <span style={{ width: `${(count / total) * 100}%` }} />
            </span>
            <span className="tally-count">
              {count} vote{count === 1 ? "" : "s"}
            </span>
          </li>
        ))}
      </ul>
      {tally.abstentions > 0 && (
        <p className="muted small">
          {tally.abstentions} {tally.abstentions === 1 ? "player" : "players"} did not vote
        </p>
      )}
    </div>
  );
}
