import { useState } from "react";
import MessageList from "../components/MessageList.jsx";

const MAX_CHARS = 300; // matches the server limit

export default function Chat({ state, onSend }) {
  const [text, setText] = useState("");

  const submit = (event) => {
    event.preventDefault();
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };

  return (
    <section className="card chat">
      <p className="task">
        Discuss who among you should be the <strong>Decision Maker</strong>.
      </p>
      <MessageList messages={state.messages} you={state.you} round={state.round} round1Tally={state.tallies["1"]} />
      <form className="composer" onSubmit={submit}>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={MAX_CHARS}
          placeholder="Type a message…"
          aria-label="Message"
          autoFocus
        />
        <button type="submit" className="primary" disabled={!text.trim()}>
          Send
        </button>
      </form>
      {state.error && <p className="error">{state.error.message}</p>}
    </section>
  );
}
