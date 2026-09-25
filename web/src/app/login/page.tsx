import { login } from "./actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const { error, next } = await searchParams;
  return (
    <main className="signin">
      <form action={login} className="pass" data-open="true">
        <div className="signin-head">
          <h1 className="signin-title">nexTix</h1>
        </div>
        <div className="notch-cut" aria-hidden>
          <span />
        </div>
        <label className="signin-field">
          <span>API token</span>
          <input
            type="password"
            name="token"
            required
            autoFocus
            autoComplete="current-password"
            aria-describedby={error ? "signin-error" : undefined}
            aria-invalid={error ? true : undefined}
          />
        </label>
        <input type="hidden" name="next" value={next ?? "/"} />
        {error ? (
          <p className="signin-error" id="signin-error" role="alert">
            That token doesn&apos;t match. Check NEXTIX_API_TOKEN in your .env file.
          </p>
        ) : null}
        <div className="signin-actions">
          <span>The value of NEXTIX_API_TOKEN</span>
          <button type="submit" className="button">
            Sign in
          </button>
        </div>
      </form>
    </main>
  );
}
