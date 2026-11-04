import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "./components/ui";

const queryClient = new QueryClient();

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <p className="p-6 text-sm text-slate-600">Multi-Tenant RAG</p>
      </ToastProvider>
    </QueryClientProvider>
  );
}
