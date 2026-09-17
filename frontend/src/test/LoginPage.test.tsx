import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthProvider } from "../shared/auth/AuthProvider";
import { LoginPage } from "../features/auth/LoginPage";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function wrap(ui: ReactElement, path = "/login") {
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/login" element={ui} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

describe("routing and login", () => {
  it("renders the login screen", () => {
    wrap(<LoginPage />);
    expect(screen.getByRole("heading", { name: /sign in to anpr/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
  });

  it("keeps the sign-in control available", async () => {
    wrap(<LoginPage />);
    await userEvent.clear(screen.getByLabelText(/email/i));
    expect(screen.getByRole("button", { name: /sign in/i })).toBeEnabled();
  });
});
