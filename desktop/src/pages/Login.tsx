import { AlertTriangle, Loader2, Zap } from "lucide-react";
import { Button } from "@ai-matrx/design-system";
import { Card, CardContent } from "@/components/ui/card";
import type { useAuth } from "@/hooks/use-auth";
import { AppVersion } from "@/lib/app-version";

type AuthActions = ReturnType<typeof useAuth>;

interface LoginProps {
  auth: Pick<
    AuthActions,
    | "signInWithOAuth"
    | "loading"
    | "error"
    | "accountConnectionUnavailable"
    | "retryAccountCleanup"
    // The daemon's own honest state. A sign-in screen that shows only "Sign in to your
    // workspace" to somebody who WAS signed in yesterday is a screen that says nothing about
    // what happened (law 4) — and after the custody cutover that was exactly the experience.
    | "snapshot"
  >;
}

/** Whether this screen owes the person an explanation, and the daemon's own words for it.
 *
 *  `signed_out` on a device that has never held a session is a clean first run and gets the plain
 *  screen. Every other state — a session this Mac could not carry over, a keychain that will not
 *  open, a daemon that is not running — has a reason the daemon wrote, and it is shown verbatim so
 *  no surface invents a second vocabulary for the same condition. */
export function loginNotice(snapshot: LoginProps["auth"]["snapshot"]): string | null {
  if (snapshot.state === "signed_out" && snapshot.user_id === null) return null;
  if (snapshot.state === "signed_in") return null;
  return snapshot.state_reason ?? null;
}

export function Login({ auth }: LoginProps) {
  const handleOAuth = async () => {
    await auth.signInWithOAuth();
    // App.tsx watches auth.oauthPending — as soon as signInWithOAuth() sets it,
    // App.tsx swaps to <OAuthPending> automatically. No local state needed.
  };

  // Email/password sign-in is gone (FS-C5b). It ran through
  // `supabase.auth.signInWithPassword`, which throws in this process now that the client is built
  // with the `accessToken` option, and the sync daemon's contract is PKCE only. A form that could
  // not submit would be a dead control; "Sign in with AI Matrx" reaches the same accounts through
  // the same provider.

  if (auth.accountConnectionUnavailable) {
    return (
      <div className="relative flex h-screen items-center justify-center overflow-hidden bg-background">
        <div className="relative z-10 w-full max-w-sm space-y-8 px-4">
          <div className="flex flex-col items-center gap-3">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/20 shadow-lg shadow-primary/10">
              <Zap className="h-7 w-7 text-primary" />
            </div>
            <div className="text-center">
              <h1 className="text-2xl font-bold tracking-tight">Matrx Local</h1>
              <p className="mt-1 text-sm text-muted-foreground">Account connection needs recovery</p>
            </div>
          </div>
          <Card className="border-border/60 shadow-xl shadow-black/5">
            <CardContent className="space-y-4 pt-6 text-center">
              <p className="text-sm text-red-500">
                {auth.error ?? "Matrx Local could not finish account cleanup. Retry account connection."}
              </p>
              <Button type="button" className="w-full" disabled={auth.loading} onClick={() => void auth.retryAccountCleanup()}>
                {auth.loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "Retry account connection"}
              </Button>
            </CardContent>
          </Card>
          <p className="text-center text-xs text-muted-foreground/50">Matrx Local &middot; <AppVersion /></p>
        </div>
      </div>
    );
  }

  const notice = loginNotice(auth.snapshot);

  // ── Normal login page ──────────────────────────────────────────────
  return (
    <div className="relative flex h-screen items-center justify-center overflow-hidden bg-background">
      {/* Ambient glow */}
      <div
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          background:
            "radial-gradient(ellipse 70% 50% at 50% -5%, hsl(var(--primary) / 0.25) 0%, transparent 65%)",
        }}
      />

      {/* Subtle grid */}
      <div
        className="pointer-events-none absolute inset-0 opacity-[0.025]"
        style={{
          backgroundImage: `linear-gradient(hsl(var(--foreground)) 1px, transparent 1px),
            linear-gradient(90deg, hsl(var(--foreground)) 1px, transparent 1px)`,
          backgroundSize: "48px 48px",
        }}
      />

      <div className="relative z-10 w-full max-w-sm space-y-8 px-4">
        {/* Brand header */}
        <div className="flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/20 shadow-lg shadow-primary/10">
            <Zap className="h-7 w-7 text-primary" />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-bold tracking-tight">Matrx Local</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {notice ? "Sign in again to continue" : "Sign in to your workspace"}
            </p>
          </div>
        </div>

        <Card className="border-border/60 shadow-xl shadow-black/5">
          <CardContent className="space-y-4 pt-6">
            {notice && (
              <div
                role="status"
                className="flex gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-left"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                <p className="text-sm text-amber-900 dark:text-amber-200">{notice}</p>
              </div>
            )}
            {/* Single AI Matrx OAuth button */}
            <Button
              className="w-full gap-2"
              onClick={handleOAuth}
              disabled={auth.loading}
            >
              {auth.loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <MatrxIcon />
              )}
              Sign in with AI Matrx
            </Button>

            {auth.error && (
              <div className="space-y-2 text-center">
                <p className="text-sm text-red-500">{auth.error}</p>
              </div>
            )}
          </CardContent>
        </Card>

        <p className="text-center text-xs text-muted-foreground/50">
          Matrx Local &middot; <AppVersion />
        </p>
      </div>
    </div>
  );
}

// AI Matrx icon — simple lightning bolt in brand style
function MatrxIcon() {
  return (
    <Zap className="h-4 w-4" />
  );
}
