export interface LocalLlmRegistrationStatus {
  available: boolean;
  registered?: boolean;
  port: number | null;
  model_name: string | null;
}

/**
 * Registration is meaningful only after the Python engine has been
 * discovered. llama-server is Rust-owned and commonly becomes ready first
 * during startup; treating that normal ordering as a failed registration
 * produces a warning every reconciliation tick until discovery completes.
 */
export function shouldAttemptLocalLlmRegistration(
  engineUrl: string | null,
  serverStatus: LocalLlmRegistrationStatus,
): boolean {
  return Boolean(engineUrl && serverStatus.available && serverStatus.port);
}

export function matchesRegisteredLocalLlm(
  engineStatus: LocalLlmRegistrationStatus | null,
  port: number,
  modelName: string,
): boolean {
  if (!engineStatus) return false;
  const registered = engineStatus.registered ?? engineStatus.available;
  return (
    registered &&
    engineStatus.port === port &&
    engineStatus.model_name === modelName
  );
}
