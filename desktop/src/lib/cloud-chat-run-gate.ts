/** Synchronous ownership gate for a single Cloud Chat run. */
export class CloudChatRunGate {
  private owner: string | null = null;

  tryStart(runId: string): boolean {
    if (this.owner) return false;
    this.owner = runId;
    return true;
  }

  finish(runId: string): boolean {
    if (this.owner !== runId) return false;
    this.owner = null;
    return true;
  }

  cancel(): void {
    this.owner = null;
  }
}
