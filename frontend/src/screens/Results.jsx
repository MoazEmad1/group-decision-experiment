import Tally from "../components/Tally.jsx";
import { METHOD_TEXT, playerName } from "../labels.js";

export default function Results({ state }) {
  const { results, you } = state;
  if (!results) return null;

  return (
    <section className="card center results">
      <p className="muted">The Decision Maker is</p>
      <h2 className="winner">{playerName(results.decision_maker, you)}</h2>
      <p>{METHOD_TEXT[results.method]}</p>
      <div className="tallies">
        <Tally title="Round 1 votes" tally={results.tallies["1"]} you={you} />
        <Tally title="Round 2 votes" tally={results.tallies["2"]} you={you} />
      </div>
      <p className="muted small">Thank you for taking part. You can close this window.</p>
    </section>
  );
}
