import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";
import { TicketView } from "@/components/TicketView";
import { fetchTicketDetail } from "@/lib/api";
import { parseTab } from "@/lib/tabs";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
};

// generateMetadata and the page read the same ticket; ask the API once per request.
const getTicket = cache((id: string) => fetchTicketDetail(id));

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const result = await getTicket((await params).id);
  if (!("ticket" in result)) return { title: "Ticket · nexTix" };
  const { ticket } = result;
  return { title: `#${ticket.issue_number} ${ticket.title} · nexTix` };
}

export default async function TicketPage({ params, searchParams }: Props) {
  const { id } = await params;
  const tab = parseTab((await searchParams).tab);
  const result = await getTicket(id);
  if ("error" in result && result.error === "not_found") notFound();

  if (!("ticket" in result)) {
    return (
      <main className="notice-page">
        <div className="pass notice-pass">
          <div className="notice-head">
            <h1 className="notice-title">Couldn&apos;t load this ticket</h1>
          </div>
          <div className="notch-cut" aria-hidden>
            <span />
          </div>
          <p className="notice-text">
            The nexTix API didn&apos;t answer. It may be restarting; try again in a moment.
          </p>
          <div className="notice-actions">
            <Link href="/" className="link-button">
              Back to the board
            </Link>
            <Link
              href={`/tickets/${encodeURIComponent(id)}${tab === "transcript" ? "" : `?tab=${tab}`}`}
              className="button"
              prefetch={false}
            >
              Try again
            </Link>
          </div>
        </div>
      </main>
    );
  }

  // Server components render once per request; the client starts its clock here so the
  // hydrated elapsed times match the HTML.
  // eslint-disable-next-line react-hooks/purity
  const renderedAt = Date.now();

  return (
    <main>
      <TicketView
        key={result.ticket.id}
        initial={result.ticket}
        renderedAt={renderedAt}
        initialTab={tab}
      />
    </main>
  );
}
