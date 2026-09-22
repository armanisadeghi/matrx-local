/**
 * The screen shown while the system browser has the sign-in (FS-C5b).
 *
 * It used to receive the OAuth callback and exchange the code itself, through two redundant
 * channels (a Tauri event and a poll of a host command that handed the webview the whole callback
 * URL). Both are gone, and so is the host command: the OS routes `aimatrx://auth/callback` to the Rust host, which forwards the code to `matrx-syncd`
 * — the only process holding the PKCE verifier (SPEC-CUSTODY S1). The daemon exchanges it and
 * emits `session.changed`; `useAuth` turns that into `isAuthenticated` and App.tsx swaps this
 * screen for the workspace.
 *
 * So this screen waits, and says so. Its one active job is to show the daemon's own sentence when
 * a sign-in cannot be completed — an expired transaction, or a link belonging to a different copy
 * of AI Matrx (S14) — rather than spinning forever at a thing that already failed.
 */

import { useEffect, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { ArrowLeft, ExternalLink, Zap } from "lucide-react";
import { Button } from "@ai-matrx/design-system";
import { formatDurationSeconds } from "@ai-matrx/kit/format";

const BRAND_COLOR = "hsl(var(--primary))";

interface OAuthPendingProps {
    onCancel: () => void;
}

function OrbitRing() {
    return (
        <div className="relative flex items-center justify-center">
            <div className="absolute h-40 w-40 rounded-full opacity-20 blur-2xl animate-pulse bg-primary" />
            <svg className="h-36 w-36 animate-spin" style={{ animationDuration: "3s" }} viewBox="0 0 144 144">
                <circle cx="72" cy="72" r="64" fill="none" stroke={BRAND_COLOR} strokeWidth="2" strokeOpacity="0.15" />
                <circle cx="72" cy="72" r="64" fill="none" stroke={BRAND_COLOR} strokeWidth="2.5" strokeDasharray="100 303" strokeLinecap="round" />
            </svg>
            <svg className="absolute h-24 w-24 animate-spin" style={{ animationDuration: "2s", animationDirection: "reverse" }} viewBox="0 0 96 96">
                <circle cx="48" cy="48" r="40" fill="none" stroke={BRAND_COLOR} strokeWidth="1.5" strokeOpacity="0.25" />
                <circle cx="48" cy="48" r="40" fill="none" stroke={BRAND_COLOR} strokeWidth="2" strokeDasharray="45 206" strokeLinecap="round" strokeOpacity="0.7" />
            </svg>
        </div>
    );
}

export function OAuthPending({ onCancel }: OAuthPendingProps) {
    const [elapsed, setElapsed] = useState(0);
    const [failure, setFailure] = useState<string | null>(null);

    useEffect(() => {
        const timer = setInterval(() => setElapsed((e) => e + 1), 1000);
        return () => clearInterval(timer);
    }, []);

    useEffect(() => {
        let unlisten: (() => void) | null = null;
        void listen<string>("syncd-sign-in-failed", (event) => setFailure(event.payload))
            .then((off) => {
                unlisten = off;
            })
            .catch(() => undefined);
        return () => unlisten?.();
    }, []);

    return (
        <div className="relative flex h-screen w-full flex-col overflow-hidden bg-background">
            <div className="pointer-events-none absolute inset-0 opacity-30 bg-[radial-gradient(ellipse_80%_60%_at_50%_-10%,hsl(var(--primary)/0.4)_0%,transparent_70%)]" />

            <header className="relative z-10 flex items-center justify-between px-6 py-5">
                <div className="flex items-center gap-2">
                    <Zap className="h-5 w-5 text-primary" />
                    <span className="text-sm font-semibold">AI Matrx</span>
                </div>
                <div className="text-xs tabular-nums text-muted-foreground">
                    {formatDurationSeconds(elapsed, { style: "clock" })}
                </div>
            </header>

            <main className="relative z-10 flex flex-1 flex-col items-center justify-center gap-8 px-6 text-center">
                {failure ? (
                    <>
                        <div>
                            <h2 className="text-xl font-semibold">That sign-in did not finish</h2>
                            {/* The daemon's own sentence, verbatim. No surface invents its own
                                wording for a state the daemon named (SPEC-ENGINE §3.1). */}
                            <p className="mx-auto mt-2 max-w-lg text-sm text-muted-foreground">{failure}</p>
                        </div>
                        <Button variant="outline" onClick={onCancel}>
                            <ArrowLeft className="mr-2 h-4 w-4" />
                            Back to sign in
                        </Button>
                    </>
                ) : (
                    <>
                        <OrbitRing />
                        <div>
                            <h2 className="text-xl font-semibold">Finish signing in in your browser</h2>
                            <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
                                We opened AI Matrx in your browser. Approve the sign-in there and this
                                window will pick it up on its own — you can leave it alone.
                            </p>
                            <p className="mx-auto mt-4 flex max-w-md items-center justify-center gap-1.5 text-xs text-muted-foreground/70">
                                <ExternalLink className="h-3 w-3" />
                                Nothing is typed into this window; your browser handles the sign-in.
                            </p>
                        </div>
                        <Button variant="ghost" size="sm" onClick={onCancel}>
                            <ArrowLeft className="mr-2 h-4 w-4" />
                            Cancel
                        </Button>
                    </>
                )}
            </main>
        </div>
    );
}
