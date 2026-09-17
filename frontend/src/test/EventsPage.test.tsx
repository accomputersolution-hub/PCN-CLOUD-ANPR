import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { EventsPage } from "../features/events/EventsPage";

vi.mock("../shared/api/client", () => ({
  api: vi.fn(async (path: string) => {
    if (path.startsWith("/events")) {
      return {
        items: [
          {
            id: "e1",
            plate_normalized: "MH12AB1234",
            local_timestamp: "2026-09-17T10:42:15+05:30",
            gate_name: "Entry Gate",
            camera_name: "Camera 1",
            direction: "ENTRY",
            ocr_confidence: 0.94,
          },
        ],
        meta: { total: 1, page: 1, page_size: 25 },
      };
    }
    return [];
  }),
}));

describe("event search", () => {
  it("lists events", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <EventsPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findAllByText("MH12AB1234")).not.toHaveLength(0);
    expect(screen.getByRole("button", { name: /search/i })).toBeInTheDocument();
  });
});
