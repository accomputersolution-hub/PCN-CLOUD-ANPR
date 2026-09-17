import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getAccessToken, wsUrl } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

const RealtimeContext = createContext<{ connected: boolean }>({ connected: false });

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!user || !getAccessToken()) return;
    let closed = false;
    let ws: WebSocket | null = null;
    let retry = 1000;
    const connect = () => {
      if (closed) return;
      ws = new WebSocket(wsUrl());
      ws.onopen = () => {
        setConnected(true);
        retry = 1000;
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data) as { type: string };
          if (msg.type === "anpr.event") {
            void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
            void queryClient.invalidateQueries({ queryKey: ["events"] });
            void queryClient.invalidateQueries({ queryKey: ["vehicles"] });
          }
          if (msg.type === "camera.status") {
            void queryClient.invalidateQueries({ queryKey: ["cameras"] });
            void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
          }
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          setTimeout(connect, retry);
          retry = Math.min(retry * 2, 15000);
        }
      };
    };
    connect();
    return () => {
      closed = true;
      ws?.close();
    };
  }, [user, queryClient]);

  return <RealtimeContext.Provider value={{ connected }}>{children}</RealtimeContext.Provider>;
}

export function useRealtime() {
  return useContext(RealtimeContext);
}
