import { useCallback, useEffect, useReducer, useRef } from "react";

// The server is the single source of truth: this hook only mirrors what it sends.
// The token lives in sessionStorage, so it survives a reload but every tab or private
// window is its own participant.
const TOKEN_KEY = "gde_token";
const CLOSE_INVALID_TOKEN = 4401;
const CLOSE_REPLACED = 4409;

function readToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function writeToken(token) {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* storage blocked: still works until reload */
  }
}

function removeToken() {
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

const initial = {
  // idle | joining | connecting | connected | reconnecting | replaced
  status: "idle",
  notice: null,
  error: null,
  you: null,
  players: [],
  phase: null,
  round: null,
  deadline: null,
  offsetMs: 0, // server clock minus client clock
  messages: [],
  myVote: null,
  tallies: {},
  results: null,
};

const offsetFrom = (serverTime) => (serverTime ? Date.parse(serverTime) - Date.now() : 0);

function applyServerFrame(state, { type, payload: p }) {
  switch (type) {
    case "state_snapshot":
      return {
        ...state,
        status: "connected",
        notice: null,
        error: null,
        you: p.you,
        players: p.players,
        phase: p.phase,
        round: p.round,
        deadline: p.deadline,
        offsetMs: offsetFrom(p.server_time),
        messages: p.messages,
        myVote: p.my_vote,
        tallies: p.tallies,
        results: p.results,
      };
    case "phase_changed":
      return {
        ...state,
        phase: p.phase,
        round: p.round,
        deadline: p.deadline,
        offsetMs: offsetFrom(p.server_time),
        myVote: null,
        error: null,
      };
    case "chat_message":
      return state.messages.some((m) => m.id === p.id) ? state : { ...state, messages: [...state.messages, p] };
    case "vote_recorded":
      return p.round === state.round ? { ...state, myVote: p.target } : state;
    case "vote_tally":
      return { ...state, tallies: { ...state.tallies, [p.round]: p } };
    case "results":
      return { ...state, results: p, tallies: p.tallies };
    case "error":
      return { ...state, error: p };
    default:
      return state;
  }
}

function reducer(state, action) {
  switch (action.type) {
    case "status":
      return { ...state, status: action.status, notice: action.notice ?? null };
    case "reset":
      return { ...initial, notice: action.notice ?? null };
    case "clear_error":
      return { ...state, error: null };
    case "server":
      return applyServerFrame(state, action.frame);
    default:
      return state;
  }
}

export function useSession() {
  const [state, dispatch] = useReducer(reducer, initial);
  const wsRef = useRef(null);
  const retry = useRef({ attempt: 0, timer: null });
  const stopped = useRef(false);

  const connect = useCallback((token) => {
    clearTimeout(retry.current.timer);
    stopped.current = false;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws?token=${encodeURIComponent(token)}`);
    wsRef.current = ws;
    if (retry.current.attempt === 0) dispatch({ type: "status", status: "connecting" });

    ws.onopen = () => {
      retry.current.attempt = 0;
    };
    ws.onmessage = (event) => {
      try {
        dispatch({ type: "server", frame: JSON.parse(event.data) });
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onclose = (event) => {
      if (wsRef.current !== ws || stopped.current) return; // superseded or unmounted
      wsRef.current = null;
      if (event.code === CLOSE_INVALID_TOKEN) {
        removeToken();
        dispatch({ type: "reset", notice: "Your previous session is no longer available. You can join a new one." });
        return;
      }
      if (event.code === CLOSE_REPLACED) {
        dispatch({ type: "status", status: "replaced" });
        return;
      }
      // Network drop or server restart: retry with backoff (a restart ends in 4401 above).
      const attempt = ++retry.current.attempt;
      dispatch({ type: "status", status: "reconnecting" });
      retry.current.timer = setTimeout(() => connect(token), Math.min(1000 * 2 ** (attempt - 1), 10000));
    };
  }, []);

  useEffect(() => {
    const token = readToken();
    if (token) connect(token); // reload: reconnect with the stored token
    return () => {
      stopped.current = true;
      clearTimeout(retry.current.timer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  const join = useCallback(async () => {
    dispatch({ type: "status", status: "joining" });
    try {
      const response = await fetch("/api/join", { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const { token } = await response.json();
      writeToken(token);
      connect(token);
    } catch {
      dispatch({ type: "reset", notice: "Could not join right now. Please try again." });
    }
  }, [connect]);

  const takeOver = useCallback(() => {
    const token = readToken();
    if (token) connect(token);
    else dispatch({ type: "reset" });
  }, [connect]);

  const send = useCallback((frame) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(frame));
  }, []);

  const sendMessage = useCallback(
    (text) => {
      dispatch({ type: "clear_error" });
      send({ type: "send_message", payload: { text } });
    },
    [send]
  );

  const castVote = useCallback(
    (target) => {
      dispatch({ type: "clear_error" });
      send({ type: "cast_vote", payload: { target } });
    },
    [send]
  );

  return { state, join, takeOver, sendMessage, castVote };
}
