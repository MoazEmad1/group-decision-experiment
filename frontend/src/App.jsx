import Countdown from "./components/Countdown.jsx";
import { PHASE_TITLES } from "./labels.js";
import Chat from "./screens/Chat.jsx";
import Join from "./screens/Join.jsx";
import Results from "./screens/Results.jsx";
import Vote from "./screens/Vote.jsx";
import Waiting from "./screens/Waiting.jsx";
import { useSession } from "./useSession.js";

export default function App() {
  const { state, join, takeOver, sendMessage, castVote } = useSession();
  const inSession = state.phase !== null;

  return (
    <main className="app">
      <header className="topbar">
        <h1>Group Decision Study</h1>
        {inSession && state.status !== "replaced" && (
          <div className="status">
            <span className="you">You are Player {state.you}</span>
            <span className="phase">{PHASE_TITLES[state.phase]}</span>
            <Countdown deadline={state.deadline} offsetMs={state.offsetMs} />
          </div>
        )}
      </header>
      {state.status === "reconnecting" && <p className="banner">Connection lost. Reconnecting…</p>}
      <Screen state={state} join={join} takeOver={takeOver} sendMessage={sendMessage} castVote={castVote} />
    </main>
  );
}

function Screen({ state, join, takeOver, sendMessage, castVote }) {
  if (state.status === "idle" || state.status === "joining") {
    return <Join onJoin={join} joining={state.status === "joining"} notice={state.notice} />;
  }
  if (state.status === "replaced") {
    return (
      <section className="card center">
        <h2>Open in another tab</h2>
        <p>This study is open in another tab or window. Please continue there.</p>
        <button onClick={takeOver}>Use this tab instead</button>
      </section>
    );
  }
  if (state.phase === null) {
    return (
      <section className="card center">
        <div className="spinner" aria-hidden="true" />
        <p className="muted">Connecting…</p>
      </section>
    );
  }

  switch (state.phase) {
    case "WAITING":
      return <Waiting />;
    case "ROUND_1_CHAT":
    case "ROUND_2_CHAT":
      return <Chat state={state} onSend={sendMessage} />;
    case "ROUND_1_VOTE":
    case "ROUND_2_VOTE":
      return <Vote state={state} onVote={castVote} />;
    case "RESULTS":
      return <Results state={state} />;
    default:
      return null;
  }
}
