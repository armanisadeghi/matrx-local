import { Loader2, Zap } from "lucide-react";
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
    | "retryAccountCleanup"
  >;
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
              Sign in to your workspace
            </p>
          </div>
        </div>

        <Card className="border-border/60 shadow-xl shadow-black/5">
          <CardContent className="space-y-4 pt-6">
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
                {auth.error.toLowerCase().includes("cleanup") && (
                  <Button type="button" variant="outline" size="sm" disabled={auth.loading} onClick={() => void auth.retryAccountCleanup()}>
                    Retry account cleanup
                  </Button>
                )}
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
