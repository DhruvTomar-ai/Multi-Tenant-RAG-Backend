import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { TenantLayout } from "./components/Layout";
import { NotFoundPanel, ToastProvider } from "./components/ui";
import { AuthProvider, RequireAuth } from "./lib/auth";
import { ApiError } from "./lib/errors";
import { LoginPage, RegisterPage } from "./pages/AuthPages";
import { TeamDetailPage, TeamsPage } from "./pages/TeamPages";
import { HomePage, MembersPage, OverviewPage } from "./pages/TenantPages";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      refetchOnWindowFocus: false,
      // A refused request will be refused again. Only short transient failures are worth one retry.
      retry: (count, error) => count < 1 && error instanceof ApiError && error.kind !== "http",
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <BrowserRouter>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />
              <Route element={<RequireAuth />}>
                <Route path="/" element={<HomePage />} />
                <Route path="/t/:tenantId" element={<TenantLayout />}>
                  <Route index element={<OverviewPage />} />
                  <Route path="members" element={<MembersPage />} />
                  <Route path="teams" element={<TeamsPage />} />
                  <Route path="teams/:teamId" element={<TeamDetailPage />} />
                  <Route path="*" element={<NotFoundPanel backTo="/" backLabel="Go to your workspace" />} />
                </Route>
                <Route path="*" element={<NotFoundPanel backTo="/" backLabel="Go to your workspace" />} />
              </Route>
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
