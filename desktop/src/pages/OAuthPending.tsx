import { useEffect, useRef, useState } from "react";
import { engine } from "@/lib/api";
import { Zap, ArrowLeft, ExternalLink, CheckCircle2, RefreshCw } from "lucide-react";
import { Button } from "@ai-matrx/design-system";

// Production redirect URI — must exactly match the redirect_uri sent in the
// authorization request (see use-auth.ts getRedirectUri()). The OS intercepts
// the aimatrx:// scheme and routes it to the Rust on_open_url handler.
const TAURI_REDIRECT_URI = "aimatrx://auth/callback";

const BRAND_COLOR = "hsl(var(--primary))";

interface OAuthPendingProps {
    onCancel: () => void;
    completeOAuthExchange: (code: string, redirectUri: string) => Promise<boolean>;
}

function WaitingDots() {
    return (
        <span className="inline-flex gap-1 items-center ml-1">
            {[0, 1, 2].map((i) => (
                <span
                    key={i}
                    className="h-1.5 w-1.5 rounded-full bg-primary/70 animate-bounce"
                    style={{ animationDelay: `${i * 0.15}s`, animationDuration: "1s" }}
                />
            ))}
        </span>
    );
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

export function OAuthPending({ onCancel, completeOAuthExchange }: OAuthPendingProps) {
    const handled = useRef(false);
    const [completed, setCompleted] = useState(false);
    const [elapsed, setElapsed] = useState(0);

    useEffect(() => {
        const t = setInterval(() => setElapsed((e) => e + 1), 1000);
        return () => clearInterval(t);
    }, []);

    // ── Callback receiver ──────────────────────────────────────────────────
    //
    // Tauri production path:
    //   1. shell.open() sent the system browser to the Supabase authorize URL
    //   2. User approved on aimatrx.com/oauth/consent
    //   3. Supabase redirected the browser to aimatrx://auth/callback?code=XXX
    //   4. OS intercepted the aimatrx:// scheme before any browser policy ran
    //      → called Rust on_open_url handler
    //   5a. Rust stored the URL in PendingOAuthUrl app state (for the race case)
    //   5b. Rust emitted Tauri event "oauth-callback" with the full URL string
    //   6. We receive via event listener OR via the 500ms polling loop (whichever wins)
    //
    // Why we need BOTH mechanisms:
    //   - On second login (after logout on macOS), the window was visible when the deep
    //     link arrived. Rust calls show_main_window() which can cause WebKit to briefly
    //     lose focus and disrupt the JS event listener, silently dropping the event.
    //   - The polling loop runs every 500ms as a parallel, defensive channel. It checks
    //     get_pending_oauth_url() which Rust populates atomically regardless of whether
    //     the event is delivered. Whichever channel delivers the code first wins;
    //     the handled.current ref ensures exactly-once handling.
    //
    // On mount we first clear any stale PendingOAuthUrl from a prior login session
    // to prevent an old code from immediately firing a new exchange.
    useEffect(() => {
        if (handled.current) return;

        let tauriUnlisten: (() => void) | null = null;
        let wsOff: (() => void) | null = null;
        let pollTimer: ReturnType<typeof setInterval> | null = null;

        function extractCode(urlStr: string): string | null {
            try {
                return new URL(urlStr).searchParams.get("code");
            } catch {
                console.warn("[OAuthPending] could not parse URL:", urlStr);
                return null;
            }
        }

        async function handleCode(code: string) {
            if (handled.current) return;
            handled.current = true;
            tauriUnlisten?.();
            wsOff?.();
            if (pollTimer !== null) {
                clearInterval(pollTimer);
                pollTimer = null;
            }

            console.log("[OAuthPending] exchanging code for tokens...");
            try {
                const ok = await completeOAuthExchange(code, TAURI_REDIRECT_URI);
                if (ok) {
                    // auth.isAuthenticated will flip true → App.tsx re-renders to
                    // the dashboard automatically. Show success briefly first.
                    setCompleted(true);
                    return;
                }
                console.error("[OAuthPending] completeOAuthExchange returned false");
            } catch (err) {
                console.error("[OAuthPending] unexpected error during exchange:", err);
            }
            // Exchange failed — clear pending state and return to login screen.
            onCancel();
        }

        async function setup() {
            let invokeAvailable = false;

            try {
                const { invoke } = await import("@tauri-apps/api/core");
                invokeAvailable = true;

                // ── Step 1: Clear any stale URL from a prior login session ──
                // This prevents a code from a previous (already-consumed) OAuth round
                // from immediately triggering a new exchange on re-login.
                await invoke<string | null>("get_pending_oauth_url");
                // (result is intentionally ignored — we just drain the stale value)

                // ── Step 2: Set up defensive polling loop ──────────────────
                // Polls every 500ms for the URL stored by Rust's on_open_url handler.
                // This is the primary fix for the second-login problem: if the deep link
                // arrives while the window is visible, Rust's show_main_window() can cause
                // WebKit to drop the event listener below. Polling always retrieves it.
                pollTimer = setInterval(async () => {
                    if (handled.current) {
                        if (pollTimer !== null) clearInterval(pollTimer);
                        return;
                    }
                    try {
                        const pendingUrl = await invoke<string | null>("get_pending_oauth_url");
                        if (pendingUrl) {
                            console.log("[OAuthPending] poll found URL:", pendingUrl);
                            const code = extractCode(pendingUrl);
                            if (code) handleCode(code);
                        }
                    } catch {
                        // ignore — Tauri may transiently unavailable during focus change
                    }
                }, 500);

            } catch {
                // Not in Tauri (e.g., web dev) — skip invoke/polling
            }

            // ── Step 3: Set up event listener (primary path, may be disrupted) ──
            // Listening for the Tauri event is faster than polling (instant delivery),
            // so we keep it as the primary path. Polling is the backstop.
            try {
                const { listen } = await import("@tauri-apps/api/event");
                const unlisten = await listen<string>("oauth-callback", (event) => {
                    if (handled.current) return;
                    console.log("[OAuthPending] received oauth-callback event:", event.payload);
                    const code = extractCode(event.payload);
                    if (code) handleCode(code);
                    else console.warn("[OAuthPending] deep-link has no code param:", event.payload);
                });
                tauriUnlisten = unlisten;
            } catch {
                // Not in Tauri or event API unavailable
            }

            // Suppress unused-variable warning if invoke wasn't available
            void invokeAvailable;
        }

        // ── Fallback: WebSocket broadcast ───────────────────────────────────
        // Used when testing with a localhost:22140 redirect URI instead of the
        // aimatrx:// deep link (e.g. when VITE_DEV_WS_AUTH=1 is set).
        wsOff = engine.on("message", (data: unknown) => {
            const msg = data as Record<string, string>;
            if (msg?.type !== "oauth-callback" || !msg.code) return;
            handleCode(msg.code);
        });

        setup();

        return () => {
            tauriUnlisten?.();
            wsOff?.();
            if (pollTimer !== null) clearInterval(pollTimer);
        };
    }, [completeOAuthExchange, onCancel]);

    return (
        <div className="relative flex h-screen w-full flex-col overflow-hidden bg-background">
            {/* Ambient gradient */}
            <div className="pointer-events-none absolute inset-0 opacity-30 bg-[radial-gradient(ellipse_80%_60%_at_50%_-10%,hsl(var(--primary)/0.4)_0%,transparent_70%)]" />

            {/* Top bar */}
            <header className="relative z-10 flex items-center justify-between px-6 py-4">
                <Button variant="ghost" size="sm" onClick={onCancel} className="gap-2 text-muted-foreground hover:text-foreground" disabled={completed}>
                    <ArrowLeft className="h-4 w-4" />
                    Cancel
                </Button>

                <div className="flex items-center gap-2">
                    <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
                        <Zap className="h-4 w-4 text-primary" />
                    </div>
                    <span className="text-sm font-semibold tracking-tight">Matrx Local</span>
                </div>

                <div className="w-24 text-right text-xs text-muted-foreground/50 tabular-nums">
                    {String(Math.floor(elapsed / 60)).padStart(2, "0")}:{String(elapsed % 60).padStart(2, "0")}
                </div>
            </header>

            {/* Main content */}
            <main className="relative z-10 flex flex-1 flex-col items-center justify-center gap-10 px-6 text-center">
                {completed ? (
                    <div className="flex flex-col items-center gap-4">
                        <div className="flex h-20 w-20 items-center justify-center rounded-full bg-emerald-500/10">
                            <CheckCircle2 className="h-10 w-10 text-emerald-500" />
                        </div>
                        <div>
                            <h2 className="text-xl font-semibold">Signed in!</h2>
                            <p className="mt-1 text-sm text-muted-foreground">Taking you to your workspace…</p>
                        </div>
                    </div>
                ) : (
                    <>
                        <div className="relative flex items-center justify-center">
                            <OrbitRing />
                            <div className="absolute flex h-16 w-16 items-center justify-center rounded-2xl shadow-lg bg-primary/10 border border-primary/20">
                                <Zap className="h-7 w-7 text-primary" />
                            </div>
                        </div>

                        <div className="max-w-xs space-y-2">
                            <h1 className="text-2xl font-bold tracking-tight">
                                Signing in with AI Matrx
                                <WaitingDots />
                            </h1>
                            <p className="text-sm leading-relaxed text-muted-foreground">
                                Complete sign-in in your browser window.
                                <br />
                                You'll be brought back here automatically.
                            </p>
                        </div>

                        <div className="w-full max-w-xs rounded-2xl border border-border bg-card/50 px-5 py-4 text-left space-y-3">
                            <Step number={1} done text="AI Matrx sign-in window opened" />
                            <Step number={2} active text="Waiting for you to complete sign-in" />
                            <Step number={3} text="You'll be returned here automatically" />
                        </div>

                        <p className="flex items-center gap-1.5 text-xs text-muted-foreground/60">
                            <ExternalLink className="h-3 w-3" />
                            Don't see the browser window? Check your taskbar.
                        </p>
                    </>
                )}
            </main>

            {/* Bottom bar */}
            <footer className="relative z-10 flex items-center justify-center gap-2 px-6 py-4">
                {!completed && (
                    <Button variant="ghost" size="sm" className="gap-2 text-xs text-muted-foreground/60 hover:text-muted-foreground" onClick={onCancel}>
                        <RefreshCw className="h-3 w-3" />
                        Try a different method
                    </Button>
                )}
            </footer>
        </div>
    );
}

function Step({ number, text, done = false, active = false }: { number: number; text: string; done?: boolean; active?: boolean }) {
    return (
        <div className="flex items-center gap-3">
            <div className={[
                "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-xs font-semibold transition-all",
                done ? "bg-primary text-primary-foreground" : active ? "bg-primary/20 text-primary ring-2 ring-primary/30 animate-pulse" : "bg-muted text-muted-foreground",
            ].join(" ")}>
                {done ? "✓" : number}
            </div>
            <span className={["text-sm", done ? "text-foreground/70 line-through" : active ? "text-foreground font-medium" : "text-muted-foreground/60"].join(" ")}>
                {text}
            </span>
        </div>
    );
}
