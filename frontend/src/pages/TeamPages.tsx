import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, ConfirmDialog, DisabledAction, EmptyState, Field, Input, Loading, LoadError, Modal, NotFoundPanel, Notice, PageTitle, Select, Textarea, useToast } from "../components/ui";
import { ROLE_LABELS, tenantKey, useAccess } from "../lib/access";
import { api } from "../lib/api";
import { friendlyMessage } from "../lib/errors";
import type { Person, RoleName, Team } from "../lib/types";
import { roleChoices } from "./TenantPages";

export function TeamsPage() {
  const { tenantId, teams, isTenantAdmin, teamRole } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () => api<Team>(`/tenants/${tenantId}/teams`, { method: "POST", json: { name: name.trim(), description: description.trim() || null } }),
    onSuccess: async (team) => {
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "teams") });
      toast(`Team "${team.name}" created.`);
      setOpen(false);
      setName("");
      setDescription("");
    },
  });
  return (
    <div className="space-y-5">
      <PageTitle
        description={isTenantAdmin ? "All teams in this tenant." : "The teams you belong to."}
        actions={isTenantAdmin ? <Button onClick={() => setOpen(true)}>New team</Button> : <DisabledAction label="New team" reason="Only tenant admins can create teams." />}
      >
        Teams
      </PageTitle>
      {teams.length === 0 ? (
        <EmptyState action={isTenantAdmin ? <Button onClick={() => setOpen(true)}>Create the first team</Button> : undefined}>{isTenantAdmin ? "No teams yet." : "You aren't in a team yet. Ask a team manager or tenant admin to add you."}</EmptyState>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {teams.map((team) => {
            const role = teamRole(team.id);
            return (
              <li key={team.id}>
                <Link to={`/t/${tenantId}/teams/${team.id}`} className="block rounded-lg border border-slate-200 bg-white p-4 shadow-sm hover:border-brand-600">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{team.name}</span>
                    <span className="flex gap-1">
                      {!team.is_active && <Badge tone="warning">Inactive</Badge>}
                      {role && <Badge>{ROLE_LABELS[role]}</Badge>}
                    </span>
                  </div>
                  {team.description && <p className="mt-1 text-sm text-slate-600">{team.description}</p>}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="Create a team">
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <Field label="Name">
            <Input required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Description (optional)">
            <Textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} />
          </Field>
          {create.isError && <Notice tone="error">{friendlyMessage(create.error)}</Notice>}
          <Button type="submit" busy={create.isPending}>
            Create team
          </Button>
        </form>
      </Modal>
    </div>
  );
}

function EditTeam({ team, onDone }: { team: Team; onDone: () => void }) {
  const { tenantId } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description ?? "");
  const save = useMutation({
    mutationFn: () => api<Team>(`/tenants/${tenantId}/teams/${team.id}`, { method: "PATCH", json: { name: name.trim(), description } }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "teams") });
      toast("Team details saved.");
      onDone();
    },
  });
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <Field label="Name">
        <Input required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="Description">
        <Textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} />
      </Field>
      {save.isError && <Notice tone="error">{friendlyMessage(save.error)}</Notice>}
      <Button type="submit" busy={save.isPending}>
        Save changes
      </Button>
    </form>
  );
}

function AddTeamMember({ team, members }: { team: Team; members: Person[] }) {
  const { tenantId, people, isTenantAdmin } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const candidates = people.filter((person) => !members.some((member) => member.id === person.id));
  const [selected, setSelected] = useState("");
  const [typedId, setTypedId] = useState("");
  const [role, setRole] = useState<RoleName>("member");
  const userId = Number(typedId || selected);
  const add = useMutation({
    // tenant_id and team_id must match the URL. The backend refuses a mismatch and takes neither from here.
    mutationFn: () => api(`/tenants/${tenantId}/teams/${team.id}/members`, { method: "POST", json: { user_id: userId, tenant_id: tenantId, team_id: team.id, role } }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "team-members", team.id) });
      toast("Team member added.");
      setSelected("");
      setTypedId("");
    },
  });
  return (
    <form
      className="mb-4 space-y-3 rounded-md bg-slate-50 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        add.mutate();
      }}
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1">
          <Field label="Person">
            <Select aria-label="Person to add" value={selected} disabled={Boolean(typedId)} onChange={(event) => setSelected(event.target.value)}>
              <option value="">Choose someone…</option>
              {candidates.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.full_name || person.username} ({person.email})
                </option>
              ))}
            </Select>
          </Field>
        </div>
        {!isTenantAdmin && (
          <div className="w-28">
            <Field label="Or user ID">
              <Input inputMode="numeric" pattern="[0-9]*" value={typedId} onChange={(event) => setTypedId(event.target.value)} />
            </Field>
          </div>
        )}
        <Field label="Team role">
          <Select aria-label="New member role" value={role} onChange={(event) => setRole(event.target.value as RoleName)}>
            {roleChoices(isTenantAdmin).map((item) => (
              <option key={item} value={item}>
                {ROLE_LABELS[item]}
              </option>
            ))}
          </Select>
        </Field>
        <Button type="submit" busy={add.isPending} disabled={!userId}>
          Add to team
        </Button>
      </div>
      {!isTenantAdmin && <p className="text-xs text-slate-500">Only tenant admins can see everyone in the tenant, so you may need the person's user ID. They must already belong to this tenant.</p>}
      {add.isError && <Notice tone="error">{friendlyMessage(add.error)}</Notice>}
    </form>
  );
}

function MemberRow({ team, person, canManage, tenantAdminView }: { team: Team; person: Person; canManage: boolean; tenantAdminView: boolean }) {
  const { tenantId, myId } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [removing, setRemoving] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "team-members", team.id) });
  const changeRole = useMutation({
    mutationFn: (role: RoleName) => api(`/tenants/${tenantId}/teams/${team.id}/members`, { method: "POST", json: { user_id: person.id, tenant_id: tenantId, team_id: team.id, role } }),
    onSuccess: async () => {
      await refresh();
      toast("Role updated.");
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/tenants/${tenantId}/teams/${team.id}/members/${person.id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await refresh();
      toast("Removed from the team.");
      setRemoving(false);
    },
  });
  // The backend doesn't yet stop a manager changing an admin. The app doesn't offer it.
  const outranked = !tenantAdminView && person.role === "admin";
  const editable = canManage && !outranked;
  return (
    <li className="py-2.5 text-sm" data-testid={`member-${person.email}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span>
          <span className="font-medium">{person.full_name || person.username}</span> <span className="text-slate-500">{person.email}</span>
          {person.id === myId && (
            <>
              {" "}
              <Badge tone="info">You</Badge>
            </>
          )}
        </span>
        <span className="flex items-center gap-2">
          {editable ? (
            <Select aria-label={`Role for ${person.email}`} className="!w-32" value={person.role} disabled={changeRole.isPending} onChange={(event) => changeRole.mutate(event.target.value as RoleName)}>
              {roleChoices(tenantAdminView).map((item) => (
                <option key={item} value={item}>
                  {ROLE_LABELS[item]}
                </option>
              ))}
            </Select>
          ) : (
            <Badge>{ROLE_LABELS[person.role]}</Badge>
          )}
          {editable && (
            <Button variant="danger" onClick={() => setRemoving(true)}>
              Remove
            </Button>
          )}
        </span>
      </div>
      {changeRole.isError && (
        <div className="mt-2">
          <Notice tone="error">{friendlyMessage(changeRole.error)}</Notice>
        </div>
      )}
      <ConfirmDialog
        open={removing}
        title="Remove from team?"
        body={`${person.full_name || person.email} will lose this team's access. Any documents they uploaded stay readable to them.`}
        confirmLabel="Remove"
        busy={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate()}
        onClose={() => setRemoving(false)}
      />
    </li>
  );
}

export function TeamDetailPage() {
  const { teamId: raw } = useParams();
  const { tenantId, teamRole, can, isTenantAdmin } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const teamId = Number(raw);
  const valid = Number.isInteger(teamId) && teamId > 0 && teamId <= 2_147_483_647;
  const teamQuery = useQuery({ queryKey: tenantKey(tenantId, "team", teamId), queryFn: () => api<Team>(`/tenants/${tenantId}/teams/${teamId}`), enabled: valid });
  const membersQuery = useQuery({ queryKey: tenantKey(tenantId, "team-members", teamId), queryFn: () => api<Person[]>(`/tenants/${tenantId}/teams/${teamId}/members`), enabled: valid });
  const setActive = useMutation({
    mutationFn: (is_active: boolean) => api<Team>(`/tenants/${tenantId}/teams/${teamId}`, { method: "PATCH", json: { is_active } }),
    onSuccess: async (team) => {
      await Promise.all([queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "teams") }), queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "team", teamId) })]);
      toast(team.is_active ? "Team reactivated." : "Team deactivated.");
    },
  });

  if (!valid) return <NotFoundPanel backTo={`/t/${tenantId}/teams`} backLabel="Go to teams" />;
  if (teamQuery.isPending || membersQuery.isPending) return <Loading />;
  if (teamQuery.isError) return <LoadError error={teamQuery.error} retry={() => void teamQuery.refetch()} backTo={`/t/${tenantId}/teams`} />;
  if (membersQuery.isError) return <LoadError error={membersQuery.error} retry={() => void membersQuery.refetch()} backTo={`/t/${tenantId}/teams`} />;

  const team = teamQuery.data;
  const members = membersQuery.data;
  const canManage = can(team.id, "team.manage");
  const role = teamRole(team.id);

  return (
    <div className="space-y-5">
      <PageTitle
        description={team.description ?? undefined}
        actions={
          canManage ? (
            <div className="flex gap-2">
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Edit team
              </Button>
              <Button variant="danger" busy={setActive.isPending} onClick={() => setActive.mutate(!team.is_active)}>
                {team.is_active ? "Deactivate" : "Reactivate"}
              </Button>
            </div>
          ) : (
            <DisabledAction label="Edit team" reason="Only team managers can edit a team." />
          )
        }
      >
        {team.name} {!team.is_active && <Badge tone="warning">Inactive</Badge>}
      </PageTitle>
      {setActive.isError && <Notice tone="error">{friendlyMessage(setActive.error)}</Notice>}
      {!team.is_active && <Notice tone="warning">This team is inactive, so its documents can't be read or searched until it's reactivated.</Notice>}
      <p className="text-sm text-slate-600">
        {role ? `Your role in this team: ${ROLE_LABELS[role]}.` : isTenantAdmin ? "You aren't a member, but tenant admins can manage every team." : ""}{" "}
        <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/documents?team=${team.id}`}>
          View this team's documents
        </Link>
      </p>
      <Card title={`Members (${members.length})`}>
        {canManage ? <AddTeamMember team={team} members={members} /> : <p className="mb-3 text-xs text-slate-500">Only team managers can add or remove members.</p>}
        {members.length === 0 ? (
          <EmptyState>No members in this team yet.</EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100">
            {members.map((person) => (
              <MemberRow key={person.id} team={team} person={person} canManage={canManage} tenantAdminView={isTenantAdmin} />
            ))}
          </ul>
        )}
      </Card>
      <Modal open={editing} onClose={() => setEditing(false)} title="Edit team">
        <EditTeam team={team} onDone={() => setEditing(false)} />
      </Modal>
    </div>
  );
}
