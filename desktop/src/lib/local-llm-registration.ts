export interface LocalLlmRegistrationStatus {
  available: boolean;
  registered?: boolean;
  port: number | null;
  model_name: string | null;
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
