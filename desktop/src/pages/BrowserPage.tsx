import { useState, useRef, useCallback, type KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight, RefreshCw, ExternalLink } from "lucide-react";

const DEFAULT_URL = "https://www.aimatrx.com";

export function BrowserPage() {
    const [url, setUrl] = useState(DEFAULT_URL);
    const [inputUrl, setInputUrl] = useState(DEFAULT_URL);
    const [key, setKey] = useState(0);
    const historyRef = useRef<string[]>([DEFAULT_URL]);
    const historyIndexRef = useRef<number>(0);

    const navigate = useCallback((target: string) => {
        // Prepend https:// if the user forgot
        let finalUrl = target.trim();
        if (finalUrl && !/^https?:\/\//i.test(finalUrl)) {
            finalUrl = "https://" + finalUrl;
        }
        // Truncate forward history when navigating to a new page
        historyRef.current = historyRef.current.slice(0, historyIndexRef.current + 1);
        historyRef.current.push(finalUrl);
        historyIndexRef.current = historyRef.current.length - 1;
        setUrl(finalUrl);
        setInputUrl(finalUrl);
        setKey((k) => k + 1);
    }, []);

    const goBack = () => {
        if (historyIndexRef.current <= 0) return;
        historyIndexRef.current -= 1;
        const prev = historyRef.current[historyIndexRef.current];
        if (!prev) return;
        setUrl(prev);
        setInputUrl(prev);
        setKey((k) => k + 1);
    };

    const goForward = () => {
        if (historyIndexRef.current >= historyRef.current.length - 1) return;
        historyIndexRef.current += 1;
        const next = historyRef.current[historyIndexRef.current];
        if (!next) return;
        setUrl(next);
        setInputUrl(next);
        setKey((k) => k + 1);
    };

    const reload = () => setKey((k) => k + 1);

    const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
        if (e.key === "Enter") navigate(inputUrl);
    };

    const canBack = historyIndexRef.current > 0;
    const canForward = historyIndexRef.current < historyRef.current.length - 1;

    return (
        <div className="flex h-full flex-col">
            {/* Toolbar */}
            <div className="flex items-center gap-1.5 border-b bg-background/80 backdrop-blur px-3 py-2 shrink-0">
                {/* Back */}
                <button
                    onClick={goBack}
                    disabled={!canBack}
                    title="Back"
                    className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                >
                    <ChevronLeft className="h-4 w-4" />
                </button>

                {/* Forward */}
                <button
                    onClick={goForward}
                    disabled={!canForward}
                    title="Forward"
                    className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                >
                    <ChevronRight className="h-4 w-4" />
                </button>

                {/* Reload */}
                <button
                    onClick={reload}
                    title="Reload"
                    className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                >
                    <RefreshCw className="h-3.5 w-3.5" />
                </button>

                {/* URL bar */}
                <input
                    type="text"
                    value={inputUrl}
                    onChange={(e) => setInputUrl(e.target.value)}
                    onKeyDown={handleKeyDown}
                    onFocus={(e) => e.currentTarget.select()}
                    placeholder="Enter URL and press Enter…"
                    className="flex-1 h-7 rounded-md border border-input bg-muted/50 px-3 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-0"
                />

                {/* Open externally */}
                <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Open in system browser"
                    className="flex h-7 w-7 items-center justify-center rounded text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                >
                    <ExternalLink className="h-3.5 w-3.5" />
                </a>
            </div>

            {/* iframe viewport */}
            <iframe
                key={key}
                src={url}
                title="Browser"
                className="flex-1 w-full border-0"
                allow="camera; microphone; clipboard-read; clipboard-write"
                sandbox="allow-same-origin allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-top-navigation-by-user-activation"
            />
        </div>
    );
}
