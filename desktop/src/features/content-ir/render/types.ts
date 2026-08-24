/**
 * The one prop contract every registered kind component in this app takes.
 *
 * `value` is the RECONSTRUCTED kind instance — the zero-loss value object the
 * envelope carries, `__kind` marker included. Accept-and-ignore the marker;
 * never strip it, and never treat its presence as a data field.
 */
export interface KindComponentProps {
  value: unknown;
  kind: string;
  /** False while the block is still arriving; a component may render partially. */
  complete: boolean;
}
