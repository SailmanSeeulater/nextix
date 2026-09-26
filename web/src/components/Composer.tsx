"use client";

import { ArrowUpRight, LoaderCircle } from "lucide-react";
import { type FormEvent, type KeyboardEvent, type RefObject, useId, useRef, useState } from "react";
import { ApiError, createTicket, parseLabels } from "@/lib/client-api";
import type { ClaudeAuth } from "@/lib/api";
import type { CreateTicketResult, RepoOption } from "@/lib/types";

type Note =
  | { tone: "info"; result: CreateTicketResult }
  | { tone: "error"; message: string }
  | null;

/**
 * The blank pass at the top of the board: one sentence in, one ticket out.
 * `morphRef` is the element the new pass grows out of in the view transition.
 */
export function Composer({
  repos,
  preferredRepo,
  morphRef,
  claudeAuth,
  onCreated,
}: {
  /** null when the repository list couldn't be loaded (API down), [] when none are connected. */
  repos: RepoOption[] | null;
  preferredRepo: string;
  /** Which Claude credential triage uses; null when unknown (API unreachable). */
  claudeAuth: ClaudeAuth | null;
  morphRef: RefObject<HTMLDivElement | null>;
  onCreated: (result: CreateTicketResult) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [labels, setLabels] = useState("");
  const [triageChoice, setTriage] = useState(true);
  // With no Claude credential on the server, filing still works, just without triage.
  const claudeOff = claudeAuth === "none";
  const triage = triageChoice && !claudeOff;
  const [chosenRepo, setChosenRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const ids = { prompt: useId(), repo: useId(), labels: useId(), claude: useId() };

  const repoNames = (repos ?? []).map((r) => r.full_name);
  const repo =
    (repoNames.includes(chosenRepo) && chosenRepo) ||
    (repoNames.includes(preferredRepo) && preferredRepo) ||
    repoNames[0] ||
    "";
  const reposUnknown = repos === null;
  const noRepos = repos !== null && repos.length === 0;
  const blocked = reposUnknown || noRepos;
  const canSubmit = !busy && !blocked && prompt.trim().length >= 3;

  function grow() {
    const el = textRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }

  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    setNote(null);
    try {
      const result = await createTicket({
        repo,
        prompt: prompt.trim(),
        labels: parseLabels(labels),
        triage,
      });
      onCreated(result);
      setPrompt("");
      setLabels("");
      setNote({ tone: "info", result });
      requestAnimationFrame(grow);
    } catch (err) {
      setNote({
        tone: "error",
        message: err instanceof ApiError ? err.message : "Something went wrong. Try again.",
      });
    } finally {
      setBusy(false);
      textRef.current?.focus();
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void submit();
    }
  }

  const busyLabel = triage ? "Writing the issue…" : "Filing…";

  return (
    <form className="composer" onSubmit={submit} aria-label="New ticket">
      <div ref={morphRef}>
        <label htmlFor={ids.prompt} className="sr-only">
          Describe a change
        </label>
        <textarea
          id={ids.prompt}
          ref={textRef}
          className="composer-prompt"
          rows={1}
          value={prompt}
          placeholder={noRepos ? "No repositories connected yet" : "Describe a change…"}
          disabled={blocked}
          readOnly={busy}
          maxLength={20000}
          onChange={(e) => {
            setPrompt(e.target.value);
            grow();
          }}
          onKeyDown={onKeyDown}
        />
      </div>

      <div className="notch-cut" aria-hidden style={{ color: "var(--ink)" }}>
        <span />
      </div>

      <div className="composer-controls">
        <label htmlFor={ids.repo} className="sr-only">
          Repository
        </label>
        <select
          id={ids.repo}
          className="select"
          value={repo}
          onChange={(e) => setChosenRepo(e.target.value)}
          disabled={blocked || busy}
        >
          {blocked ? (
            <option value="">{reposUnknown ? "Repositories unavailable" : "No repositories"}</option>
          ) : null}
          {repoNames.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <label htmlFor={ids.labels} className="sr-only">
          Labels, comma separated
        </label>
        <input
          id={ids.labels}
          className="composer-labels"
          value={labels}
          onChange={(e) => setLabels(e.target.value)}
          placeholder="Labels: ui, docs"
          disabled={blocked}
          readOnly={busy}
        />
        <label className="switch">
          <input
            type="checkbox"
            role="switch"
            checked={triage}
            onChange={(e) => setTriage(e.target.checked)}
            disabled={blocked || busy || claudeOff}
            aria-describedby={claudeOff ? ids.claude : undefined}
          />
          Write it up with Claude
          {claudeAuth === "subscription" || claudeAuth === "api_key" ? (
            <span className="switch-meta">
              {claudeAuth === "subscription" ? "on your plan" : "API key"}
            </span>
          ) : null}
        </label>
        <button
          type="submit"
          className="button"
          disabled={!canSubmit}
          aria-busy={busy || undefined}
          style={{ marginLeft: "auto" }}
        >
          {busy ? <LoaderCircle size={16} className="spin" aria-hidden /> : null}
          {busy ? busyLabel : "Create ticket"}
        </button>
      </div>

      <div aria-live="polite">
        {reposUnknown ? (
          <p className="composer-note">
            Can&apos;t reach nexTix to load your repositories. Filing is paused until it
            reconnects.
          </p>
        ) : noRepos ? (
          <p className="composer-note">
            Install the nexTix GitHub App on a repository. It shows up here as soon as GitHub
            tells nexTix about it.
          </p>
        ) : note?.tone === "error" ? (
          <p className="composer-note" data-tone="error">
            {note.message}
          </p>
        ) : note?.tone === "info" ? (
          <p className="composer-note">
            {note.result.needs_input ? (
              <>
                Filed {note.result.ticket.repo}#{note.result.ticket.issue_number} in Needs Input.
                Claude asked: “{note.result.clarifying_question}”{" "}
                <a href={note.result.issue_url} target="_blank" rel="noreferrer">
                  Answer on GitHub
                  <ArrowUpRight size={13} strokeWidth={2.5} aria-hidden style={{ display: "inline" }} />
                </a>
              </>
            ) : (
              <>
                Filed {note.result.ticket.repo}#{note.result.ticket.issue_number}:{" "}
                {note.result.ticket.title}.{" "}
                <a href={note.result.issue_url} target="_blank" rel="noreferrer">
                  View issue
                  <ArrowUpRight size={13} strokeWidth={2.5} aria-hidden style={{ display: "inline" }} />
                </a>
              </>
            )}
          </p>
        ) : busy && triage ? (
          <p className="composer-note">Claude is turning this into an issue. This can take a minute.</p>
        ) : null}
      </div>
      {claudeOff && !blocked ? (
        <p className="composer-note" id={ids.claude}>
          Claude isn&apos;t set up on the server, so tickets are filed exactly as you write
          them. Add CLAUDE_CODE_OAUTH_TOKEN (your Claude plan) or ANTHROPIC_API_KEY to .env
          and restart to have Claude write them up.
        </p>
      ) : null}
    </form>
  );
}
