import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider } from "./shared/auth/AuthProvider";
import { RealtimeProvider } from "./shared/ws/RealtimeProvider";
import { AppLayout } from "./layouts/AppLayout";
import { GuestOnly, RequireAuth } from "./layouts/guards";
import { AuthLayout } from "./layouts/AuthLayout";
import { LoginPage } from "./features/auth/LoginPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { CamerasPage } from "./features/cameras/CamerasPage";
import { EventsPage } from "./features/events/EventsPage";
import { VehicleDetailPage, VehicleSearchPage } from "./features/vehicles/VehiclePages";
import { MockAnprPage } from "./features/mock/MockAnprPage";
import { SitesPage } from "./features/sites/SitesPage";
import { ReportsPage } from "./features/reports/ReportsPage";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <RealtimeProvider>
            <Routes>
              <Route element={<GuestOnly />}>
                <Route element={<AuthLayout />}>
                  <Route path="/login" element={<LoginPage />} />
                </Route>
              </Route>
              <Route element={<RequireAuth />}>
                <Route element={<AppLayout />}>
                  <Route path="/" element={<DashboardPage />} />
                  <Route path="/events" element={<EventsPage />} />
                  <Route path="/vehicles" element={<VehicleSearchPage />} />
                  <Route path="/vehicles/:plate" element={<VehicleDetailPage />} />
                  <Route path="/cameras" element={<CamerasPage />} />
                  <Route path="/mock" element={<MockAnprPage />} />
                  <Route path="/sites" element={<SitesPage />} />
                  <Route path="/reports" element={<ReportsPage />} />
                </Route>
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
            <Toaster position="top-center" richColors />
          </RealtimeProvider>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
