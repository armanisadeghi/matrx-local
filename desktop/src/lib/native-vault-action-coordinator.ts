/** Orders presentation from independent native Vault actions. */
export function createNativeVaultActionCoordinator() {
  let latest = 0;
  return {
    start() {
      latest += 1;
      return latest;
    },
    isCurrent(generation: number) {
      return generation === latest;
    },
    invalidate() {
      latest += 1;
    },
  };
}
