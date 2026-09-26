import Link from "next/link";

export default function TicketNotFound() {
  return (
    <main className="notice-page">
      <div className="pass notice-pass">
        <div className="notice-head">
          <h1 className="notice-title">No such ticket</h1>
        </div>
        <div className="notch-cut" aria-hidden>
          <span />
        </div>
        <p className="notice-text">
          It may have been removed from the board, or the link is incomplete.
        </p>
        <div className="notice-actions">
          <span />
          <Link href="/" className="button">
            Back to the board
          </Link>
        </div>
      </div>
    </main>
  );
}
