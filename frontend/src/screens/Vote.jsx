import MessageList from "../components/MessageList.jsx";
import { playerName } from "../labels.js";

export default function Vote({ state, onVote }) {
  const { players, you, myVote, round } = state;

  return (
    <>
      <section className="card">
        <h2>Who should be the Decision Maker?</h2>
        <p className="muted">Round {round} vote. You can change your vote until the time runs out.</p>
        <div className="vote-options">
          {players.map((label) => (
            <button
              key={label}
              className={`vote-option${myVote === label ? " selected" : ""}`}
              aria-pressed={myVote === label}
              onClick={() => onVote(label)}
            >
              {playerName(label, you)}
            </button>
          ))}
        </div>
        <p className="small">
          {myVote ? (
            <>
              Your vote: <strong>{playerName(myVote, you)}</strong>
            </>
          ) : (
            <span className="muted">You have not voted yet. Not voting counts as an abstention.</span>
          )}
        </p>
        {state.error && <p className="error">{state.error.message}</p>}
      </section>
      <section className="card history">
        <h3>Discussion so far</h3>
        <MessageList messages={state.messages} you={you} round={round} round1Tally={state.tallies["1"]} />
      </section>
    </>
  );
}
