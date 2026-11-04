import { useQueryClient } from "@tanstack/react-query";
import { Building2, FileText, Home, LogOut, MessageSquare, Search, Users, UsersRound } from "lucide-react";
import { Link, NavLink, Outlet, useMatch, useNavigate } from "react-router-dom";
import { ROLE_LABELS, TenantBoundary, useAccess, useTenants } from "../lib/access";
import { useAuth } from "../lib/auth";
import { Badge, Button, Select } from "./ui";

function TenantSwitcher() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { tenantId } = useAccess();
  const tenants = useTenants().data ?? [];

  return (
    <label className="flex items-center gap-2 text-sm">
      <Building2 className="size-4 shrink-0 text-slate-500" aria-hidden />
      <span className="sr-only">Switch tenant</span>
      <Select
        aria-label="Switch tenant"
        className="!w-44"
        value={tenantId}
        onChange={(event) => {
          // Drop everything first, so nothing from this tenant can render under the next one.
          queryClient.removeQueries({ predicate: (query) => query.queryKey[0] === "t" });
          navigate(`/t/${event.target.value}`);
        }}
      >
        {tenants.map((tenant) => (
          <option key={tenant.id} value={tenant.id} disabled={!tenant.is_active}>
            {tenant.name}
            {tenant.is_active ? "" : " (inactive)"}
          </option>
        ))}
      </Select>
    </label>
  );
}

/** Always says who you are acting as: the tenant, the team on this page, and your role there. */
function ContextBadge() {
  const { tenant, teams, isTenantAdmin, teamRole, teamName } = useAccess();
  const match = useMatch("/t/:tenantId/teams/:teamId/*");
  const pageTeamId = match ? Number(match.params.teamId) : null;
  const pageTeamRole = pageTeamId !== null ? teamRole(pageTeamId) : undefined;
  const joined = teams.filter((team) => teamRole(team.id));
  const showPageTeam = pageTeamId !== null && teams.some((team) => team.id === pageTeamId);

  return (
    <div data-testid="context-badge" className="flex flex-wrap items-center gap-1.5 text-xs">
      <Badge tone="info">Tenant: {tenant.name}</Badge>
      <Badge tone={isTenantAdmin ? "success" : "neutral"}>{isTenantAdmin ? "Tenant admin" : "Tenant member"}</Badge>
      {showPageTeam ? (
        <Badge>
          Team: {teamName(pageTeamId)}
          {pageTeamRole ? ` · ${ROLE_LABELS[pageTeamRole]}` : isTenantAdmin ? " · via tenant admin" : ""}
        </Badge>
      ) : joined.length > 0 ? (
        joined.map((team) => (
          <Badge key={team.id}>
            {team.name} · {ROLE_LABELS[teamRole(team.id)!]}
          </Badge>
        ))
      ) : (
        !isTenantAdmin && <Badge tone="warning">No team yet</Badge>
      )}
    </div>
  );
}

function Sidebar() {
  const { tenantId, isTenantAdmin } = useAccess();
  const items = [
    { to: `/t/${tenantId}`, label: "Overview", icon: Home, end: true },
    { to: `/t/${tenantId}/chat`, label: "Chat", icon: MessageSquare },
    { to: `/t/${tenantId}/search`, label: "Search", icon: Search },
    { to: `/t/${tenantId}/documents`, label: "Documents", icon: FileText },
    { to: `/t/${tenantId}/teams`, label: "Teams", icon: UsersRound },
    ...(isTenantAdmin ? [{ to: `/t/${tenantId}/members`, label: "People", icon: Users }] : []),
  ];
  return (
    <nav aria-label="Main" className="w-full shrink-0 md:w-52">
      <ul className="flex gap-1 overflow-x-auto md:flex-col">
        {items.map(({ to, label, icon: Icon, end }) => (
          <li key={to}>
            <NavLink end={end} to={to} className={({ isActive }) => `flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium ${isActive ? "bg-brand-50 text-brand-700" : "text-slate-600 hover:bg-slate-100"}`}>
              <Icon className="size-4" aria-hidden />
              {label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function UserMenu() {
  const { me, logout } = useAuth();
  return (
    <div className="flex items-center gap-3">
      <span className="hidden text-right text-xs leading-tight lg:block" data-testid="current-user">
        <span className="block font-medium text-slate-800">{me?.full_name || me?.username}</span>
        <span className="block text-slate-500">{me?.email}</span>
      </span>
      <Button variant="ghost" onClick={logout}>
        <LogOut className="size-4" aria-hidden />
        Sign out
      </Button>
    </div>
  );
}

export function PlainHeader() {
  return (
    <header className="flex items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-3">
      <Link to="/" className="font-semibold">
        Multi-Tenant RAG
      </Link>
      <UserMenu />
    </header>
  );
}

export function TenantLayout() {
  return (
    <TenantBoundary>
      <div className="flex min-h-full flex-col">
        <header className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 py-3">
          <div className="flex flex-wrap items-center gap-4">
            <Link to="/" className="font-semibold">
              Multi-Tenant RAG
            </Link>
            <TenantSwitcher />
            <ContextBadge />
          </div>
          <UserMenu />
        </header>
        <div className="mx-auto flex w-full max-w-7xl flex-1 flex-col gap-6 p-5 md:flex-row">
          <Sidebar />
          <main className="min-w-0 flex-1">
            <Outlet />
          </main>
        </div>
      </div>
    </TenantBoundary>
  );
}
