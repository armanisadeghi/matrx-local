/**
 * Browser Lab — test in-app browser strategies.
 *
 * Tab 1 – Basic iframe   : raw <iframe src>, fastest but blocked by X-Frame-Options
 * Tab 2 – Tauri Fetch    : Rust reqwest → blob URL, bypasses all browser restrictions
 *                          (only works inside the Tauri desktop app)
 *
 * The FastAPI proxy approach was evaluated but removed: it can't rehydrate
 * client-side JS frameworks (React/Next.js), so CSS and page logic break even
 * on simple sites.  The /fetch-proxy/extract endpoint is kept on the backend
 * as a scraping primitive (plain text extraction).
 */
import { useState } from "react";
import { BrowserPage } from "./BrowserPage";
import { TauriFetchBrowser } from "./TauriFetchBrowser";
import { Globe, Zap, Info, ChevronRight } from "lucide-react";

interface Tab {
    id: string;
    label: string;
    icon: typeof Globe;
    badge: string;
    badgeClass: string;
    description: string;
    pros: string[];
    cons: string[];
    desktopOnly?: boolean;
    component: React.FC;
}

const TABS: Tab[] = [
    {
        id: "basic",
        label: "Basic iframe",
        icon: Globe,
        badge: "Baseline",
        badgeClass: "bg-zinc-500/15 text-zinc-500 border-zinc-500/20",
        description:
            "Raw HTML iframe — fastest path with no overhead. The browser enforces X-Frame-Options and CSP frame-ancestors headers sent by the target site.",
        pros: [
            "Zero latency — direct load, no middleman",
            "Native JS execution and full CSS support",
            "Cookies / sessions maintained end-to-end",
            "Works for any site that permits framing",
        ],
        cons: [
            "Blocked by X-Frame-Options: DENY / SAMEORIGIN",
            "Blocked by CSP frame-ancestors 'none'",
            "No way to extract content from a blocked page",
        ],
        component: BrowserPage,
    },
    {
        id: "tauri",
        label: "Tauri Fetch",
        icon: Zap,
        badge: "Desktop only",
        badgeClass: "bg-violet-500/15 text-violet-600 dark:text-violet-400 border-violet-500/20",
        description:
            "Uses a Rust command (reqwest) invoked via Tauri's IPC bridge. The page is fetched entirely outside the browser security model, decoded from base64, and displayed as a blob URL. Most powerful approach for scraping.",
        pros: [
            "Zero browser security restrictions — X-Frame-Options ignored",
            "Native Rust speed, no Python dependency",
            "Extracted plain text available immediately (no extra request)",
            "Foundation for cookie jar injection, custom TLS, proxy chaining",
        ],
        cons: [
            "Requires the Tauri desktop app (not available in browser dev mode)",
            "blob: iframe has limited same-origin access for JS",
            "SPAs won't fully hydrate (static render only)",
        ],
        desktopOnly: true,
        component: TauriFetchBrowser,
    },
];

export function BrowserLab() {
    const [activeTab, setActiveTab] = useState("basic");
    const [showInfo, setShowInfo] = useState(false);

    const active = TABS.find((t) => t.id === activeTab)!;
    const ActiveComponent = active.component;

    return (
        <div className="flex h-full flex-col">
            {/* Tab bar */}
            <div className="flex items-center border-b bg-background/80 backdrop-blur px-3 shrink-0">
                {TABS.map((tab) => {
                    const Icon = tab.icon;
                    const isActive = tab.id === activeTab;
                    return (
                        <button
                            key={tab.id}
                            onClick={() => setActiveTab(tab.id)}
                            className={`flex items-center gap-2 px-4 py-2.5 text-xs font-medium border-b-2 transition-colors ${isActive
                                    ? "border-primary text-foreground"
                                    : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
                                }`}
                        >
                            <Icon className="h-3.5 w-3.5" />
                            {tab.label}
                            <span className={`text-[10px] px-1.5 py-0.5 rounded border ${tab.badgeClass}`}>
                                {tab.badge}
                            </span>
                        </button>
                    );
                })}

                <div className="ml-auto pb-0.5">
                    <button
                        onClick={() => setShowInfo(!showInfo)}
                        className={`flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors ${showInfo ? "text-foreground bg-accent" : ""
                            }`}
                        title="Toggle approach info"
                    >
                        <Info className="h-3.5 w-3.5" />
                    </button>
                </div>
            </div>

            {/* Collapsible info banner */}
            {showInfo && (
                <div className="border-b bg-muted/40 px-4 py-3 shrink-0">
                    <p className="text-xs font-medium text-foreground mb-2">{active.description}</p>
                    <div className="flex gap-8">
                        <div>
                            <p className="text-[10px] font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wide mb-1.5">
                                Advantages
                            </p>
                            <ul className="space-y-0.5">
                                {active.pros.map((p) => (
                                    <li key={p} className="flex items-start gap-1.5 text-[11px] text-foreground/75">
                                        <ChevronRight className="matrx-line-icon-xs h-3 w-3 mt-0.5 text-emerald-500" />
                                        {p}
                                    </li>
                                ))}
                            </ul>
                        </div>
                        <div>
                            <p className="text-[10px] font-semibold text-destructive uppercase tracking-wide mb-1.5">
                                Limitations
                            </p>
                            <ul className="space-y-0.5">
                                {active.cons.map((c) => (
                                    <li key={c} className="flex items-start gap-1.5 text-[11px] text-foreground/75">
                                        <ChevronRight className="matrx-line-icon-xs h-3 w-3 mt-0.5 text-destructive/70" />
                                        {c}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </div>
            )}

            {/* Active browser component */}
            <div className="flex-1 overflow-hidden">
                <ActiveComponent />
            </div>
        </div>
    );
}
