/**
 * MessageMarkdown — THE chat markdown renderer for this app.
 *
 * Extracted from `ChatMessages.tsx` so the Content IR render layer draws prose
 * through the SAME renderer the chat does. A second markdown component here
 * would be a fork of the thing users read most, and the `renderValue` /
 * `markdown`-kind seams exist precisely so the host supplies this once.
 */

import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

export function MessageMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={{
        pre: ({ children }) => (
          <pre className="overflow-x-auto rounded-md bg-muted p-3 text-[0.8125rem]">
            {children}
          </pre>
        ),
        code: ({ className, children, ...props }) => {
          const isInline = !className;
          if (isInline) {
            return (
              <code
                className="rounded bg-muted px-1.5 py-0.5 text-[0.8125rem] font-mono"
                {...props}
              >
                {children}
              </code>
            );
          }
          return (
            <code className={className} {...props}>
              {children}
            </code>
          );
        },
      }}
    >
      {text}
    </ReactMarkdown>
  );
}
