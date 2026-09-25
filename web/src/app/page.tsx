import { Board } from "@/components/Board";
import { fetchHealth, fetchRepos, fetchTickets } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function BoardPage() {
  const [health, tickets, repos] = await Promise.all([
    fetchHealth(),
    fetchTickets(),
    fetchRepos(),
  ]);

  // Server components render once per request; the client starts its clock here so the
  // hydrated elapsed times match the HTML.
  // eslint-disable-next-line react-hooks/purity
  const renderedAt = Date.now();

  return (
    <main>
      <Board
        initial={tickets ?? []}
        repos={repos}
        apiState={health === null ? "unreachable" : health.status}
        loadError={tickets === null}
        renderedAt={renderedAt}
      />
    </main>
  );
}
