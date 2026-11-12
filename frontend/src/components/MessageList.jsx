import { useEffect, useRef } from "react";
import { playerName } from "../labels.js";
import Tally from "./Tally.jsx";

// Round-1 messages, then (once round 2 has started) a divider, the round-1 tally and round-2 messages.
export default function MessageList({ messages, you, round, round1Tally }) {
  const boxRef = useRef(null);
  const round1 = messages.filter((m) => m.round === 1);
  const round2 = messages.filter((m) => m.round === 2);
  const showRound2 = (round ?? 0) >= 2 || round2.length > 0;

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [messages.length, showRound2]);

  return (
    <div className="messages" ref={boxRef}>
      {round1.length === 0 && !showRound2 && <p className="muted">No messages yet.</p>}
      {round1.map((m) => (
        <Message key={m.id} message={m} you={you} />
      ))}
      {showRound2 && (
        <>
          <div className="divider">
            <span>Round 2</span>
          </div>
          <Tally title="Round 1 votes" tally={round1Tally} you={you} />
          {round2.map((m) => (
            <Message key={m.id} message={m} you={you} />
          ))}
        </>
      )}
    </div>
  );
}

function Message({ message, you }) {
  const mine = message.from === you;
  return (
    <div className={`msg${mine ? " mine" : ""}`}>
      <span className="msg-from">{playerName(message.from, you)}</span>
      <span className="msg-text">{message.text}</span>
    </div>
  );
}
