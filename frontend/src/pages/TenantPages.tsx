import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { PlainHeader } from "../components/Layout";
import { Badge, Button, Card, EmptyState, Field, Input, Loading, LoadError, Modal, NotFoundPanel, Notice, PageTitle, Select, Textarea, useToast } from "../components/ui";
import { ROLE_LABELS, ROLES, tenantKey, useAccess, useTenants } from "../lib/access";
import { api } from "../lib/api";
import { ApiError, friendlyMessage } from "../lib/errors";
import type { Person, RoleName, Tenant } from "../lib/types";

function slugify(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);
}

function CreateTenantForm({ onCreated }: { onCreated: (tenant: Tenant) => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const create = useMutation({
    mutationFn: () => api<Tenant>("/tenants", { method: "POST", json: { name: name.trim(), slug: slug || slugify(name), description: description.trim() || null } }),
    onSuccess: async (tenant) => {
      await queryClient.invalidateQueries({ queryKey: ["tenants"] });
      onCreated(tenant);
    },
  });
  return (
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
      <Field label="Short web name" hint="Letters, numbers and dashes. Leave empty to create one from the name.">
        <Input maxLength={100} value={slug} placeholder={slugify(name)} onChange={(event) => setSlug(event.target.value)} />
      </Field>
      <Field label="Description (optional)">
        <Textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} />
      </Field>
      {create.isError && <Notice tone="error">{friendlyMessage(create.error)}</Notice>}
      <Button type="submit" busy={create.isPending}>
        Create tenant
      </Button>
    </form>
  );
}

/** "/": sends you to your first tenant, or offers to create one. */
export function HomePage() {
  const navigate = useNavigate();
  const tenants = useTenants();
  if (tenants.isPending) return <Loading />;
  if (tenants.isError) return <LoadError error={tenants.error} retry={() => void tenants.refetch()} />;
  const first = tenants.data.find((tenant) => tenant.is_active);
  if (first) return <Navigate to={`/t/${first.id}`} replace />;
  return (
    <div className="min-h-full">
      <PlainHeader />
      <div className="mx-auto mt-10 max-w-lg space-y-4 px-4">
        <PageTitle description="You aren't part of a tenant yet. Ask a tenant admin to add you using your email, or create your own.">Welcome</PageTitle>
        <Card title="Create a tenant">
          <CreateTenantForm onCreated={(tenant) => navigate(`/t/${tenant.id}`)} />
        </Card>
      </div>
    </div>
  );
}

function EditTenantForm({ onDone }: { onDone: () => void }) {
  const { tenant } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(tenant.name);
  const [slug, setSlug] = useState(tenant.slug);
  const [description, setDescription] = useState(tenant.description ?? "");
  const save = useMutation({
    mutationFn: () => api<Tenant>(`/tenants/${tenant.id}`, { method: "PATCH", json: { name: name.trim(), slug: slug.trim(), description } }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["tenants"] });
      toast("Tenant details saved.");
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
      <Field label="Short web name">
        <Input required maxLength={100} value={slug} onChange={(event) => setSlug(event.target.value)} />
      </Field>
      <Field label="Description">
        <Textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
      </Field>
      {save.isError && <Notice tone="error">{friendlyMessage(save.error)}</Notice>}
      <Button type="submit" busy={save.isPending}>
        Save changes
      </Button>
    </form>
  );
}

export function OverviewPage() {
  const { tenant, tenantId, teams, isTenantAdmin, teamRole } = useAccess();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const joined = teams.filter((team) => teamRole(team.id));
  return (
    <div className="space-y-5">
      <PageTitle
        description={tenant.description ?? undefined}
        actions={
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => setCreating(true)}>
              New tenant
            </Button>
            {isTenantAdmin && (
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Edit details
              </Button>
            )}
          </div>
        }
      >
        {tenant.name}
      </PageTitle>
      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="Your access">
          {isTenantAdmin ? (
            <p className="text-sm text-slate-700">You are a tenant admin, so you can manage this tenant and every team in it.</p>
          ) : joined.length > 0 ? (
            <ul className="space-y-1 text-sm">
              {joined.map((team) => (
                <li key={team.id}>
                  <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/teams/${team.id}`}>
                    {team.name}
                  </Link>{" "}
                  <Badge>{ROLE_LABELS[teamRole(team.id)!]}</Badge>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-slate-700">You aren't in a team yet. You can still read any document that was shared with you.</p>
          )}
        </Card>
        <Card title="Start here">
          <ul className="space-y-1 text-sm">
            <li>
              <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/chat`}>
                Ask a question
              </Link>{" "}
              about your documents
            </li>
            <li>
              <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/search`}>
                Search
              </Link>{" "}
              for a passage
            </li>
            <li>
              <Link className="font-medium text-brand-600 hover:underline" to={`/t/${tenantId}/documents`}>
                Browse documents
              </Link>
            </li>
          </ul>
        </Card>
      </div>
      <Modal open={editing} onClose={() => setEditing(false)} title="Edit tenant details">
        <EditTenantForm onDone={() => setEditing(false)} />
      </Modal>
      <Modal open={creating} onClose={() => setCreating(false)} title="Create a tenant">
        <CreateTenantForm onCreated={(created) => navigate(`/t/${created.id}`)} />
      </Modal>
    </div>
  );
}

export function roleChoices(isTenantAdmin: boolean): RoleName[] {
  // Mirrors the backend rule that nobody can grant permissions they don't hold.
  return isTenantAdmin ? ROLES : ROLES.filter((role) => role !== "admin");
}

export function MembersPage() {
  const { tenantId, isTenantAdmin, people } = useAccess();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<RoleName>("member");
  const add = useMutation({
    mutationFn: () => api<Person>(`/tenants/${tenantId}/members`, { method: "POST", json: { email: email.trim(), role } }),
    onSuccess: async (person) => {
      await queryClient.invalidateQueries({ queryKey: tenantKey(tenantId, "members") });
      toast(`${person.email} is now a ${ROLE_LABELS[person.role].toLowerCase()} of this tenant.`);
      setEmail("");
    },
  });

  if (!isTenantAdmin) return <NotFoundPanel backTo={`/t/${tenantId}`} backLabel="Go to the overview" />;

  const members = [...people].sort((a, b) => (a.full_name ?? a.email).localeCompare(b.full_name ?? b.email));
  const addError = add.error instanceof ApiError && add.error.status === 404 ? "We couldn't add that person. Check that they've created an account with this email." : friendlyMessage(add.error);

  return (
    <div className="space-y-5">
      <PageTitle description="Everyone in this tenant. Adding someone who is already here changes their tenant role.">People</PageTitle>
      <Card title="Add a person">
        <form
          className="flex flex-wrap items-end gap-3"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            add.mutate();
          }}
        >
          <div className="min-w-56 flex-1">
            <Field label="Email">
              <Input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
            </Field>
          </div>
          <Field label="Tenant role">
            <Select value={role} onChange={(event) => setRole(event.target.value as RoleName)}>
              {ROLES.map((item) => (
                <option key={item} value={item}>
                  {ROLE_LABELS[item]}
                </option>
              ))}
            </Select>
          </Field>
          <Button type="submit" busy={add.isPending}>
            Add person
          </Button>
        </form>
        {add.isError && (
          <div className="mt-3">
            <Notice tone="error">{addError}</Notice>
          </div>
        )}
        <p className="mt-3 text-xs text-slate-500">They need an account first. To remove someone from a tenant, the backend has no option yet.</p>
      </Card>
      <Card title={`Members (${members.length})`}>
        {members.length === 0 ? (
          <EmptyState>No members yet.</EmptyState>
        ) : (
          <ul className="divide-y divide-slate-100">
            {members.map((person) => (
              <li key={person.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                <span>
                  <span className="font-medium">{person.full_name || person.username}</span> <span className="text-slate-500">{person.email}</span>
                </span>
                <span className="flex items-center gap-2">
                  <Badge>{ROLE_LABELS[person.role]}</Badge>
                  <span className="text-xs text-slate-500">User ID {person.id}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
