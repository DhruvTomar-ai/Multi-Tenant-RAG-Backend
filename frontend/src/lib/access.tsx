import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useLayoutEffect, useMemo, type ReactNode } from "react";
import { Outlet, useParams } from "react-router-dom";
import { Loading, LoadError, NotFoundPanel } from "../components/ui";
import { api } from "./api";
import { useAuth } from "./auth";
import { ApiError } from "./errors";
import type { Person, RoleName, Team, Tenant } from "./types";

// A copy of DEFAULT_ROLE_PERMISSIONS in app/security/permissions.py. It only decides which actions to
// show. The backend checks every request, so a wrong answer here can never grant anything.
export type Permission = "team.manage" | "document.upload" | "document.read";

const ROLE_PERMISSIONS: Record<RoleName, Permission[]> = {
  admin: ["team.manage", "document.upload", "document.read"],
  manager: ["team.manage", "document.upload", "document.read"],
  member: ["document.upload", "document.read"],
  viewer: ["document.read"],
};

export const ROLE_LABELS: Record<RoleName, string> = { admin: "Admin", manager: "Manager", member: "Member", viewer: "Viewer" };
export const ROLES: RoleName[] = ["admin", "manager", "member", "viewer"];

/** Every key under this prefix is removed when the tenant changes. */
export const tenantKey = (tenantId: number, ...rest: unknown[]) => ["t", tenantId, ...rest];

export function useTenants() {
  return useQuery({ queryKey: ["tenants"], queryFn: () => api<Tenant[]>("/tenants") });
}

interface TenantAccess {
  tenantId: number;
  tenant: Tenant;
  myId: number;
  teams: Team[];
  isTenantAdmin: boolean;
  /** Your membership role in a team, if you have one. */
  teamRole: (teamId: number) => RoleName | undefined;
  /** Whether to show an action. The backend still decides. */
  can: (teamId: number, permission: Permission) => boolean;
  teamName: (teamId: number) => string;
  /** Everyone this user is allowed to see in the tenant, for choosing people by name. */
  people: Person[];
}

const AccessContext = createContext<TenantAccess | null>(null);

export function useAccess() {
  const value = useContext(AccessContext);
  if (!value) throw new Error("useAccess must be used inside TenantBoundary");
  return value;
}

function isRefusal(error: unknown) {
  return error instanceof ApiError && error.isNoAccess;
}

/**
 * The backend has no "what is my role" endpoint, so it is worked out from what the user may read:
 * the tenant member list answers only for tenant admins, and each team's member list includes your role.
 */
function TenantAccessProvider({ tenant, children }: { tenant: Tenant; children: ReactNode }) {
  const { me } = useAuth();
  const myId = me!.id;
  const tenantId = tenant.id;

  const teamsQuery = useQuery({ queryKey: tenantKey(tenantId, "teams"), queryFn: () => api<Team[]>(`/tenants/${tenantId}/teams`) });
  const membersQuery = useQuery({
    queryKey: tenantKey(tenantId, "members"),
    queryFn: async () => {
      try {
        return await api<Person[]>(`/tenants/${tenantId}/members`);
      } catch (error) {
        if (isRefusal(error)) return null;
        throw error;
      }
    },
  });
  const teams = useMemo(() => teamsQuery.data ?? [], [teamsQuery.data]);
  const teamMembers = useQueries({
    queries: teams.map((team) => ({
      queryKey: tenantKey(tenantId, "team-members", team.id),
      queryFn: async () => {
        try {
          return await api<Person[]>(`/tenants/${tenantId}/teams/${team.id}/members`);
        } catch (error) {
          if (isRefusal(error)) return [];
          throw error;
        }
      },
    })),
  });

  const teamMemberData = teamMembers.map((query) => query.data);
  const teamMembersVersion = teamMembers.map((query) => query.dataUpdatedAt).join();
  const teamMembersLoading = teamMembers.some((query) => query.isPending);
  const teamMembersError = teamMembers.find((query) => query.isError)?.error;

  const value = useMemo<TenantAccess>(() => {
    const isTenantAdmin = membersQuery.data != null;
    const roles = new Map<number, RoleName>();
    const people = new Map<number, Person>();
    teams.forEach((team, index) => {
      for (const person of teamMemberData[index] ?? []) {
        people.set(person.id, person);
        if (person.id === myId) roles.set(team.id, person.role);
      }
    });
    for (const person of membersQuery.data ?? []) people.set(person.id, person);
    return {
      tenantId,
      tenant,
      myId,
      teams,
      isTenantAdmin,
      teamRole: (teamId) => roles.get(teamId),
      can: (teamId, permission) => {
        if (isTenantAdmin) return true;
        const role = roles.get(teamId);
        return role !== undefined && ROLE_PERMISSIONS[role].includes(permission);
      },
      teamName: (teamId) => teams.find((team) => team.id === teamId)?.name ?? `Team ${teamId}`,
      people: [...people.values()],
    };
    // teamMemberData is a new array each render, so its version stands in for it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenant, tenantId, myId, teams, membersQuery.data, teamMembersVersion]);

  if (teamsQuery.isPending || membersQuery.isPending || teamMembersLoading) return <Loading label="Checking your access…" />;
  const failure = teamsQuery.error ?? membersQuery.error ?? teamMembersError;
  if (failure) return <LoadError error={failure} retry={() => void Promise.all([teamsQuery.refetch(), membersQuery.refetch(), ...teamMembers.map((query) => query.refetch())])} backTo="/" />;
  return <AccessContext.Provider value={value}>{children}</AccessContext.Provider>;
}

/** Every page under /t/:tenantId. The tenant must be one of the user's, and comes only from the URL. */
export function TenantBoundary({ children }: { children?: ReactNode }) {
  const queryClient = useQueryClient();
  const { tenantId: rawTenantId } = useParams();
  const tenantId = /^[1-9]\d{0,9}$/.test(rawTenantId ?? "") ? Number(rawTenantId) : null;
  const tenantsQuery = useTenants();

  // Removes the previous tenant's data on every path into a new tenant, including the back button.
  useLayoutEffect(() => {
    queryClient.removeQueries({ predicate: (query) => query.queryKey[0] === "t" && query.queryKey[1] !== tenantId });
  }, [queryClient, tenantId]);

  if (tenantsQuery.isPending) return <Loading />;
  if (tenantsQuery.isError) return <LoadError error={tenantsQuery.error} retry={() => void tenantsQuery.refetch()} />;
  const tenant = tenantsQuery.data.find((item) => item.id === tenantId && item.is_active);
  if (!tenant) return <NotFoundPanel backTo="/" backLabel="Go to your workspace" />;
  return (
    <TenantAccessProvider key={tenant.id} tenant={tenant}>
      {children ?? <Outlet />}
    </TenantAccessProvider>
  );
}
