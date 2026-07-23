"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BarVisualizer,
  LiveKitRoom,
  RoomAudioRenderer,
  useConnectionState,
  useRoomContext,
  useVoiceAssistant,
} from "@livekit/components-react";
import {
  ConnectionState,
  RoomEvent,
  type Participant,
  type TranscriptionSegment,
} from "livekit-client";
import "@livekit/components-styles";

type Conn = { serverUrl: string; token: string };
type Line = { id: string; role: "user" | "agent"; text: string; final: boolean; ts: number };

const STATE_LABEL: Record<string, string> = {
  disconnected: "Disconnected",
  connecting: "Connecting…",
  initializing: "Warming up…",
  listening: "Listening",
  thinking: "Thinking…",
  speaking: "Speaking",
};

export default function Page() {
  const [conn, setConn] = useState<Conn | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startCall = useCallback(async () => {
    setConnecting(true);
    setError(null);
    try {
      const res = await fetch("/api/token", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to start the call");
      setConn({ serverUrl: data.serverUrl, token: data.token });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setConnecting(false);
    }
  }, []);

  const endCall = useCallback(() => setConn(null), []);

  return (
    <main className="wrap">
      <header className="hd">
        <div className="brand">
          CROSSUB<span className="brandLight"> Voice Agent</span>
        </div>
        <div className="sub">Phase 0 tester · English + 中文</div>
        <nav className="nav">
          <span className="navlink active">Voice tester</span>
          <a className="navlink" href="/messaging">
            Text / messaging →
          </a>
        </nav>
      </header>

      {!conn ? (
        <section className="lobby">
          <div className="orb" aria-hidden />
          <button className="cta" onClick={startCall} disabled={connecting}>
            {connecting ? "Connecting…" : "Start call"}
          </button>
          {error && <p className="err">{error}</p>}
          <p className="hint">
            First start the agent worker in a terminal:
            <span className="cmd">uv run crossub-voice-agent dev</span>
          </p>
        </section>
      ) : (
        <LiveKitRoom
          serverUrl={conn.serverUrl}
          token={conn.token}
          connect
          audio
          video={false}
          onDisconnected={endCall}
          className="room"
        >
          <RoomAudioRenderer />
          <CallView onEnd={endCall} />
        </LiveKitRoom>
      )}
    </main>
  );
}

function CallView({ onEnd }: { onEnd: () => void }) {
  const { state, audioTrack } = useVoiceAssistant();
  const connState = useConnectionState();
  const lines = useTranscript();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [lines]);

  const connected = connState === ConnectionState.Connected;

  return (
    <section className="call">
      <div className="statusbar">
        <span className={`dot ${connected ? "on" : "off"}`} />
        <span className="state">{STATE_LABEL[state] ?? state}</span>
        <button className="end" onClick={onEnd}>
          End call
        </button>
      </div>

      <div className="viz">
        <BarVisualizer state={state} trackRef={audioTrack} barCount={7} className="bars" />
      </div>

      <div className="transcript" ref={scrollRef}>
        {lines.length === 0 ? (
          <p className="empty">Say hello — the agent will greet you in English or 中文.</p>
        ) : (
          lines.map((l) => (
            <div key={l.id} className={`line ${l.role} ${l.final ? "final" : "interim"}`}>
              <span className="who">{l.role === "user" ? "You" : "CROSSUB AI"}</span>
              <span className="txt">{l.text}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

/** Accumulate LiveKit transcription segments into an ordered, de-duplicated list. */
function useTranscript(): Line[] {
  const room = useRoomContext();
  const [byId, setById] = useState<Record<string, Line>>({});

  useEffect(() => {
    if (!room) return;
    const handler = (segments: TranscriptionSegment[], participant?: Participant) => {
      const role: "user" | "agent" = participant?.isLocal ? "user" : "agent";
      setById((prev) => {
        const next = { ...prev };
        for (const s of segments) {
          next[s.id] = {
            id: s.id,
            role,
            text: s.text,
            final: s.final,
            ts: s.firstReceivedTime ?? Date.now(),
          };
        }
        return next;
      });
    };
    room.on(RoomEvent.TranscriptionReceived, handler);
    return () => {
      room.off(RoomEvent.TranscriptionReceived, handler);
    };
  }, [room]);

  return useMemo(() => Object.values(byId).sort((a, b) => a.ts - b.ts), [byId]);
}
