import { fetchHealth } from "@/lib/api";

const COLUMNS = ["Todo", "Doing", "Needs Input", "Failed", "In Review", "Done"] as const;

export const dynamic = "force-dynamic";

export default async function BoardPage() {
  const health = await fetchHealth();

  return (
    <main className="mx-auto max-w-7xl p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">nexTix</h1>
        <ApiStatus
          state={health === null ? "unreachable" : health.status}
          version={health?.version}
          checks={health?.checks}
        />
      </header>

      <section className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        {COLUMNS.map((name) => (
          <div
            key={name}
            className="rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900"
          >
            <h2 className="mb-2 text-sm font-medium text-neutral-600 dark:text-neutral-300">
              {name}
            </h2>
            <p className="text-xs text-neutral-400">No tickets yet</p>
          </div>
        ))}
      </section>
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
