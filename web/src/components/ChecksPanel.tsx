"use client";

import {
  ArrowUpRight,
  ChevronsDownUp,
  ChevronsUpDown,
  CircleCheck,
  CircleMinus,
  CircleX,
  Clock,
  FlaskConical,
  LoaderCircle,
  type LucideIcon,
  PlugZap,
  TriangleAlert,
} from "lucide-react";
import { memo, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { otherReviewErrors, tailText, testReport, type TestReport } from "@/lib/artifacts";
import { formatSeconds } from "@/lib/board";
import {
  checkDuration,
  checkOutcome,
  safeLink,
  shortSha,
  sortChecks,
  summarizeChecks,
  type CheckTone,
} from "@/lib/checks";
import { ApiError, fetchArtifactText } from "@/lib/client-api";
import { isActiveStatus } from "@/lib/runs";
import type { RunDetail, TicketChecks } from "@/lib/types";
import { ReviewErrors } from "./ReviewErrors";

/** Lines of test output shown before "Show all". */
const TAIL_LINES = 60;

/**
 * Checks: the selected run's own test report first (it is what the agent's change was
 * checked against), then the CI check runs GitHub reports for the PR's head commit.
 */
export function ChecksPanel({
  run,
  checks,
  prNumber,
  prHeadSha,
  now,
}: {
  run: RunDetail | null;
  checks: TicketChecks | null;
  prNumber: number | null;
  prHeadSha: string | null;
  now: number;
}) {
  const testsId = useId();
  const ciId = useId();
  const reviewErrors = run?.review_errors;
  const artifacts = run?.artifacts;
  const errors = useMemo(
    () => otherReviewErrors({ artifacts, review_errors: reviewErrors }),
    [artifacts, reviewErrors],
  );
  const report = useMemo(() => testReport({ artifacts }), [artifacts]);

  return (
    <div className="checks">
      <section className="checks-section" aria-labelledby={testsId}>
        <h3 className="panel-heading" id={testsId}>
          Tests
        </h3>
        <ReviewErrors errors={errors} />
        <TestResult run={run} report={report} />
      </section>

      <section className="checks-section" aria-labelledby={ciId}>
        <CiChecks
          headingId={ciId}
          checks={checks}
          prNumber={prNumber}
          prHeadSha={prHeadSha}
          now={now}
        />
      </section>
    </div>
  );
}

function TestResult({ run, report }: { run: RunDetail | null; report: TestReport | null }) {
  if (!run) {
    return <p className="panel-empty">No run yet, so nothing has been tested.</p>;
  }
  const tests = run.tests;
  if (!tests) {
    if (isActiveStatus(run.status)) {
      return (
        <p className="panel-empty">
          <Clock size={16} strokeWidth={2.5} aria-hidden />
          Tests run after the agent finishes.
        </p>
      );
    }
    if (run.status !== "succeeded") {
      // A run that failed or stopped early may never have reached its tests.
      return (
        <div className="panel-empty">
          <FlaskConical size={16} strokeWidth={2.5} aria-hidden />
          <p>
            <strong>No test results for this run.</strong> Tests run after the agent when the
            repo&apos;s <code>.nextix.yml</code> has a <code>test</code> command.
          </p>
        </div>
      );
    }
    return (
      <div className="panel-empty">
        <FlaskConical size={16} strokeWidth={2.5} aria-hidden />
        <p>
          <strong>No tests configured.</strong> Add a <code>test</code> command to the
          repo&apos;s <code>.nextix.yml</code> and the next run will run it after the agent.
        </p>
      </div>
    );
  }

  const passed = tests.passed;
  return (
    <div className="test-result" data-passed={passed}>
      <p className="test-status" role="status">
        {passed ? (
          <CircleCheck size={20} strokeWidth={2.5} aria-hidden />
        ) : (
          <CircleX size={20} strokeWidth={2.5} aria-hidden />
        )}
        {passed ? "Tests passed" : "Tests failed"}
      </p>
      <dl className="test-fields">
        <div className="test-field test-field-wide">
          <dt>Command</dt>
          <dd>
            <code>{tests.command}</code>
          </dd>
        </div>
        <div className="test-field">
          <dt>Exit code</dt>
          <dd className="tabular">{tests.exit_code}</dd>
        </div>
        <div className="test-field">
          <dt>Duration</dt>
          <dd className="tabular">{formatSeconds(tests.duration_s)}</dd>
        </div>
      </dl>
      {report?.url ? (
        <TestOutput
          key={report.url}
          url={report.url}
          truncated={report.truncated}
          failed={!passed}
        />
      ) : (
        <p className="test-output-note">The test output wasn&apos;t saved for this run.</p>
      )}
    </div>
  );
}

type OutputState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; text: string };

/** The stored output, end first: a failure is reported at the bottom of a test log. */
const TestOutput = memo(function TestOutput({
  url,
  truncated,
  failed,
}: {
  url: string;
  truncated: boolean;
  failed: boolean;
}) {
  const [state, setState] = useState<OutputState>({ status: "loading" });
  const [reloads, setReloads] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchArtifactText(url, undefined, controller.signal).then(
      (text) => setState({ status: "ok", text }),
      (err: unknown) => {
        if (controller.signal.aborted) return;
        setState({
          status: "error",
          message: err instanceof ApiError ? err.message : "Couldn't load the test output.",
        });
      },
    );
    return () => controller.abort();
  }, [url, reloads]);

  const text = state.status === "ok" ? state.text : "";
  const tail = useMemo(() => tailText(text, TAIL_LINES), [text]);
  const clipped = tail.hiddenLines > 0 || tail.text.length < text.trimEnd().length;

  // Opening the whole log keeps its end in view, where the tail was.
  useLayoutEffect(() => {
    const pre = preRef.current;
    if (pre) pre.scrollTop = pre.scrollHeight;
  }, [expanded, text]);

  if (state.status === "loading") {
    return (
      <p className="test-output-note">
        <LoaderCircle size={14} strokeWidth={2.5} className="spin" aria-hidden />
        Loading the test output…
      </p>
    );
  }
  if (state.status === "error") {
    return (
      <p className="test-output-note" data-tone="error">
        <TriangleAlert size={14} strokeWidth={2.5} aria-hidden />
        {state.message}
        <button
          type="button"
          className="link-button"
          onClick={() => {
            setState({ status: "loading" });
            setReloads((n) => n + 1);
          }}
        >
          Try again
        </button>
      </p>
    );
  }
  if (text.trim() === "") {
    return <p className="test-output-note">The test command printed nothing.</p>;
  }

  return (
    <div className="test-output">
      <div className="test-output-bar">
        <p className="test-output-label">
          Output
          <span className="test-output-meta tabular">
            {expanded || !clipped
              ? `${tail.totalLines.toLocaleString("en-US")} ${tail.totalLines === 1 ? "line" : "lines"}`
              : `last ${(tail.totalLines - tail.hiddenLines).toLocaleString("en-US")} of ${tail.totalLines.toLocaleString("en-US")} lines`}
          </span>
        </p>
        {clipped ? (
          <button
            type="button"
            className="link-button test-output-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? (
              <ChevronsDownUp size={14} strokeWidth={2.5} aria-hidden />
            ) : (
              <ChevronsUpDown size={14} strokeWidth={2.5} aria-hidden />
            )}
            {expanded ? "Show the end only" : "Show all output"}
          </button>
        ) : null}
      </div>
      {truncated ? (
        <p className="test-output-note">
          The output was longer than nexTix keeps; this is its last 200 KB.
        </p>
      ) : null}
      <pre
        ref={preRef}
        className="t-raw test-output-text"
        data-error={failed || undefined}
        data-expanded={expanded || undefined}
        tabIndex={0}
        aria-label="Test output"
      >
        {expanded ? text : tail.text}
      </pre>
    </div>
  );
});

const TONE_ICONS: Record<CheckTone, LucideIcon> = {
  failed: CircleX,
  passed: CircleCheck,
  running: LoaderCircle,
  waiting: Clock,
  neutral: CircleMinus,
};

function CiChecks({
  headingId,
  checks,
  prNumber,
  prHeadSha,
  now,
}: {
  headingId: string;
  checks: TicketChecks | null;
  prNumber: number | null;
  prHeadSha: string | null;
  now: number;
}) {
  const runs = useMemo(() => sortChecks(checks?.runs ?? []), [checks]);
  const summary = summarizeChecks(runs);
  const sha = shortSha(prHeadSha);

  return (
    <>
      <div className="panel-heading-row">
        <h3 className="panel-heading" id={headingId}>
          CI
        </h3>
        {sha ? (
          <span className="panel-heading-meta tabular">
            {runs.length ? `${summary.text} · ` : ""}commit {sha}
          </span>
        ) : null}
      </div>

      {prNumber === null ? (
        <p className="panel-empty">No pull request yet, so there is no commit for CI to check.</p>
      ) : checks && !checks.connected ? (
        <div className="panel-note" role="note">
          <PlugZap size={16} strokeWidth={2.5} aria-hidden />
          <div>
            <p className="panel-note-title">CI isn&apos;t connected</p>
            <p>
              Give the nexTix GitHub App the <strong>Checks: read</strong> permission to see CI
              results here.
            </p>
          </div>
        </div>
      ) : runs.length === 0 ? (
        <p className="panel-empty">No CI checks reported for this commit.</p>
      ) : null}

      {runs.length > 0 ? (
        <ul className="check-list">
          {runs.map((check) => {
            const outcome = checkOutcome(check);
            const Icon = TONE_ICONS[outcome.tone];
            const duration = checkDuration(check, now);
            const link = safeLink(check.html_url);
            return (
              <li key={check.id} className="check-row" data-tone={outcome.tone}>
                <Icon
                  size={16}
                  strokeWidth={2.5}
                  className={outcome.tone === "running" ? "check-icon spin" : "check-icon"}
                  aria-hidden
                />
                <span className="check-main">
                  <span className="check-name">{check.name}</span>
                  {check.app_name ? <span className="check-app">{check.app_name}</span> : null}
                </span>
                <span className="check-state">
                  <span className="check-word">{outcome.word}</span>
                  <span className="check-time tabular">{duration ?? ""}</span>
                </span>
                {link ? (
                  <a className="check-link" href={link} target="_blank" rel="noreferrer">
                    Details
                    <ArrowUpRight size={14} strokeWidth={2.5} aria-hidden />
                    <span className="sr-only"> for {check.name} (opens in a new tab)</span>
                  </a>
                ) : (
                  <span className="check-link" aria-hidden />
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </>
  );
}
