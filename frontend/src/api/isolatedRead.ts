export interface IsolatedRead<T> {
  value: T | null;
  error: string | null;
}

export async function isolatedRead<T>(promise: Promise<T>): Promise<IsolatedRead<T>> {
  try {
    return { value: await promise, error: null };
  } catch (error: unknown) {
    return {
      value: null,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}
