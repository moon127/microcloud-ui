import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { finishSignIn } from "../api";

/** Where Google sends the browser back after signing in to a mailbox (D96). The session
 * cookie is SameSite=strict, so it didn't come with Google's redirect; this page finishes
 * the sign-in with an ordinary request, as the signed-in user who started it. */
export default function OAuthCallbackPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [failed, setFailed] = useState<string | null>(null);
  const done = useRef(false);
  const state = params.get("state") ?? "";
  const code = params.get("code") ?? "";
  const refused = params.get("error");
  // only an OAuth error code is shown: a link could carry any text (D96 review)
  const problem = refused
    ? `Google didn't sign in${/^[a-z_]{1,40}$/.test(refused) ? `: ${refused}` : ""}.`
    : !state || !code
      ? "This page finishes a sign-in started from a notification channel."
      : null;
  useEffect(() => {
    if (problem || done.current) return; // once, even when React runs effects twice
    done.current = true;
    finishSignIn(state, code).then(
      (r) => navigate(r.path, { replace: true }),
      (e: Error) => setFailed(e.message),
    );
  }, [problem, state, code, navigate]);
  const error = problem ?? failed;
  return (
    <section aria-label="Signing in">
      <h1>Signing in to the mail server</h1>
      {error ? (
        <>
          <p role="alert">{error}</p>
          <p>
            <Link to="/settings#notifications">Back to the notification channels</Link>
          </p>
        </>
      ) : (
        <p role="status">Finishing the sign-in…</p>
      )}
    </section>
  );
}
