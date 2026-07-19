import { useState } from "react";
import { Loader2, Zap } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Card, CardContent } from "@/components/ui/card";
import type { useAuth } from "@/hooks/use-auth";
import { AppVersion } from "@/lib/app-version";

type AuthActions = ReturnType<typeof useAuth>;

interface LoginProps {
  auth: Pick<
    AuthActions,
    | "signInWithOAuth"
    | "signInWithEmail"
    | "loading"
    | "error"
  >;
}

export function Login({ auth }: LoginProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const handleOAuth = async () => {
    await auth.signInWithOAuth();
    // App.tsx watches auth.oauthPending — as soon as signInWithOAuth() sets it,
    // App.tsx swaps to <OAuthPending> automatically. No local state needed.
  };

  const handleEmailSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password) return;
    await auth.signInWithEmail(email, password);
  };

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

            {/* Divider */}
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <Separator />
              </div>
              <div className="relative flex justify-center text-xs uppercase">
                <span className="bg-card px-2 text-muted-foreground">
                  or continue with email
                </span>
              </div>
            </div>

            {/* Email / password */}
            <form onSubmit={handleEmailSignIn} className="space-y-3">
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="you@example.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="email"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  placeholder="Password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
              <Button
                type="submit"
                variant="outline"
                className="w-full"
                disabled={auth.loading || !email || !password}
              >
              {auth.loading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "Sign in"
              )}
              </Button>
            </form>

            {auth.error && (
              <p className="text-center text-sm text-red-500">{auth.error}</p>
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
