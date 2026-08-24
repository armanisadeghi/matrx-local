/**
 * THE ONE PLATFORM TOKEN for this client.
 *
 * `content_ir.kind_component.platform` is a CHECK-constrained vocabulary
 * (`web | vite | react-native | chrome-extension | desktop | html-js`). This
 * app resolves as `desktop`; a host that lies here renders the wrong component
 * everywhere.
 *
 * React-free so the stream loop and the block builder can import it.
 */
export const CONTENT_IR_PLATFORM = "desktop" as const;
