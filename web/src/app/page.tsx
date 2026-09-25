import { Board } from "@/components/Board";
import { fetchHealth, fetchTickets } from "@/lib/api";
import { logout } from "./login/actions";

export const dynamic = "force-dynamic";

export default async function BoardPage() {
  const [health, tickets] = await Promise.all([fetchHealth(), fetchTickets()]);

  return (
    <main className="mx-auto max-w-[1600px] p-6">
      <header className="mb-6 flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">nexTix</h1>
        <div className="flex items-center gap-4">
          <ApiStatus
            state={health === null ? "unreachable" : health.status}
            version={health?.version}
            checks={health?.checks}
          />
          <form action={logout}>
            <button type="submit" className="text-sm text-neutral-500 hover:underline">
              Sign out
            </button>
          </form>
        </div>
      </header>

      {tickets === null ? (
        <p className="mb-4 rounded-md bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
          Could not load tickets from the API. The board will fill in once it reconnects.
        </p>
      ) : null}
      <Board initial={tickets ?? []} />
    </main>
  );
}

function ApiStatus({
  state,
  version,
  checks,
}: {
  state: "ok" | "error" | "unreachable";
  version?: string;
  checks?: Record<string, string>;
}) {
  const color =
    state === "ok" ? "bg-emerald-500" : state === "error" ? "bg-amber-500" : "bg-red-500";
  const label =
    state === "ok"
      ? `API ok${version ? ` · v${version}` : ""}`
      : state === "error"
        ? `API degraded: ${Object.entries(checks ?? {})
            .filter(([, v]) => v !== "ok")
            .map(([k]) => k)
            .join(", ")}`
        : "API unreachable";
  return (
    <div className="flex items-center gap-2 text-sm text-neutral-600 dark:text-neutral-300">
      <span className={`inline-block h-2.5 w-2.5 rounded-full ${color}`} />
      {label}
    </div>
  );
}
