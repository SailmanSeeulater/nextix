import { login } from "./actions";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const { error, next } = await searchParams;
  return (
    <main className="flex min-h-screen items-center justify-center p-6">
      <form
        action={login}
        className="w-full max-w-sm space-y-4 rounded-lg border border-neutral-200 bg-white p-6 dark:border-neutral-800 dark:bg-neutral-900"
      >
        <h1 className="text-xl font-semibold tracking-tight">nexTix</h1>
        <label className="block space-y-1 text-sm">
          <span className="text-neutral-600 dark:text-neutral-300">API token</span>
          <input
            type="password"
            name="token"
            required
            autoFocus
            autoComplete="current-password"
            className="w-full rounded-md border border-neutral-300 bg-transparent px-3 py-2 dark:border-neutral-700"
          />
        </label>
        <input type="hidden" name="next" value={next ?? "/"} />
        {error ? <p className="text-sm text-red-600">That token didn&apos;t match.</p> : null}
        <button
          type="submit"
          className="w-full rounded-md bg-neutral-900 px-3 py-2 text-sm font-medium text-white dark:bg-neutral-100 dark:text-neutral-900"
        >
          Sign in
        </button>
        <p className="text-xs text-neutral-500">
          Use the value of <code>NEXTIX_API_TOKEN</code>.
        </p>
      </form>
    </main>
  );
}
