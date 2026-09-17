import { beforeEach, describe, expect, it, vi } from "vitest";

describe("api client errors", () => {
  beforeEach(() => {
    vi.resetModules();
    localStorage.clear();
  });

  it("includes endpoint and status in development mode", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ code: "internal_error", message: "An unexpected error occurred" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const { api, ApiError } = await import("../shared/api/client");
    await expect(api("/dashboard/summary")).rejects.toMatchObject({
      status: 500,
      path: "/dashboard/summary",
    });
    try {
      await api("/cameras");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as Error).message).toContain("500");
      expect((err as Error).message).toContain("/cameras");
      expect((err as Error).message).toContain("An unexpected error occurred");
    }
  });
});
