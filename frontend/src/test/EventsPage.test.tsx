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
            registry_match: {
              known: true,
              status: "resident",
              registry_status: "active",
              plate_normalized: "MH12AB1234",
              person_name: "Asha",
              flat_room_unit: "B-204",
              category: "resident",
              active: true,
            },
          },
          {
            id: "e2",
            plate_normalized: "MH14XY9999",
            local_timestamp: "2026-09-17T11:00:00+05:30",
            gate_name: "Exit Gate",
            camera_name: "Camera 2",
            direction: "EXIT",
            ocr_confidence: 0.88,
            registry_match: {
              known: true,
              status: "guest",
              registry_status: "inactive",
              plate_normalized: "MH14XY9999",
              person_name: "Ravi",
              flat_room_unit: "C-12",
              category: "guest",
              active: false,
            },
          },
          {
            id: "e3",
            plate_normalized: "MH01ZZ0001",
            local_timestamp: "2026-09-17T12:00:00+05:30",
            gate_name: "Entry Gate",
            camera_name: "Camera 1",
            direction: "ENTRY",
            ocr_confidence: 0.7,
            registry_match: {
              known: false,
              status: "unknown",
              registry_status: "unknown",
              plate_normalized: "MH01ZZ0001",
            },
          },
        ],
        meta: { total: 3, page: 1, page_size: 25 },
      };
    }
    return [];
  }),
}));

describe("event search", () => {
  it("lists events with registry details", async () => {
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
    expect(screen.getByPlaceholderText(/resident name/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/b-204/i)).toBeInTheDocument();
    expect(screen.getAllByText("Asha").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/INACTIVE/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  });
});
