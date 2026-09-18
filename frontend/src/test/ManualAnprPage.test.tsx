import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { ManualAnprPage } from "../features/manual/ManualAnprPage";

const apiMock = vi.fn();

vi.mock("../shared/api/client", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

function wrap(ui: ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("Manual ANPR page", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation((path: string, init?: { method?: string }) => {
      if (path === "/cameras") {
        return Promise.resolve([{ id: "cam-1", name: "Entry Cam", direction: "ENTRY" }]);
      }
      if (path === "/manual-anpr/analyze" && init?.method === "POST") {
        return Promise.resolve({
          capture_id: "cap-1",
          organization_id: "org-1",
          site_id: "site-1",
          camera_id: "cam-1",
          vehicle_detected: true,
          plate_detected: true,
          detected_plate: "MH20DV2366",
          raw_ocr: "MH20DV2366",
          normalized_plate: "MH20DV2366",
          ocr_confidence: 0.96,
          plate_confidence: 0.68,
          combined_confidence: 0.86,
          matches_indian_pattern: true,
          ocr_confident: true,
          processing_ms: 100,
          bbox: [],
          plate_crop_jpeg_base64: null,
          error: null,
          event_created: false,
        });
      }
      return Promise.reject(new Error(`unexpected ${path}`));
    });
  });

  it("renders capture controls and editable confirmation after analyze", async () => {
    wrap(<ManualAnprPage />);
    expect(await screen.findByRole("heading", { name: /manual anpr/i })).toBeInTheDocument();
    expect(screen.getByText(/upload or capture/i)).toBeInTheDocument();

    const file = new File([new Uint8Array([1, 2, 3])], "car.jpg", { type: "image/jpeg" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await userEvent.upload(input, file);

    await userEvent.click(screen.getByRole("button", { name: /run anpr/i }));
    expect(await screen.findByDisplayValue("MH20DV2366")).toBeInTheDocument();
    expect(screen.getByText(/indian pattern/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm event/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();

    const plateInput = screen.getByDisplayValue("MH20DV2366");
    await userEvent.clear(plateInput);
    await userEvent.type(plateInput, "MH20DV0001");
    expect(plateInput).toHaveValue("MH20DV0001");
  });
});
