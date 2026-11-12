import { useEffect, useState } from "react";

// Remaining time from the server deadline, corrected by the server/client clock offset.
export default function Countdown({ deadline, offsetMs }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, []);

  if (!deadline) return null;
  const seconds = Math.ceil(Math.max(0, Date.parse(deadline) - (now + offsetMs)) / 1000);
  const text = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;

  return (
    <span className={`countdown${seconds <= 10 ? " urgent" : ""}`} aria-label={`${seconds} seconds left`}>
      {text}
    </span>
  );
}
