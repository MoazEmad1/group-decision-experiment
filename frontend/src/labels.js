// Display helpers. Participants are only ever shown as "Player X": the client never
// receives a participant type, so nothing here can reveal who is human or AI.

export const playerName = (label, you) => `Player ${label}${label === you ? " (you)" : ""}`;

export const PHASE_TITLES = {
  WAITING: "Waiting room",
  ROUND_1_CHAT: "Round 1 · Discussion",
  ROUND_1_VOTE: "Round 1 · Vote",
  ROUND_2_CHAT: "Round 2 · Discussion",
  ROUND_2_VOTE: "Round 2 · Vote",
  RESULTS: "Results",
};

export const METHOD_TEXT = {
  round2: "Decided by the round 2 votes.",
  combined: "Round 2 was tied, so the votes from both rounds were combined.",
  random: "Still tied after combining both rounds, so the Decision Maker was drawn at random.",
};
