import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { DashboardPage } from "../features/dashboard/DashboardPage";

vi.mock("../shared/ws/RealtimeProvider", () => ({
  useRealtime: () => ({ connected: false }),
}));

vi.mock("../shared/api/client", () => ({
  api: vi.fn(async (path: string) => {
    if (path.startsWith("/dashboard/summary")) {
      return {
        entries_today: 148,
        exits_today: 129,
        currently_inside: 19,
        detections_today: 277,
        cameras_active: 1,
        cameras_offline: 1,
        cameras_total: 2,
        timezone: "Asia/Kolkata",
        recent_events: [
          {
            id: "1",
            plate_normalized: "MH12AB1234",
            local_timestamp: "2026-09-17T10:42:15+05:30",
            gate_name: "Entry Gate",
            direction: "ENTRY",
            ocr_confidence: 0.94,
          },
        ],
      };
    }
    return [];
  }),
}));

describe("dashboard", () => {
  it("renders summary tiles", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <DashboardPage />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText("148")).toBeInTheDocument();
    expect(screen.getByText("MH12AB1234")).toBeInTheDocument();
    expect(screen.getByText(/currently inside/i)).toBeInTheDocument();
  });
});
